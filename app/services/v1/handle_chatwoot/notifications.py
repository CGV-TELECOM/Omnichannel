from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ChatwootLegacyMap,
    ChatwootMapResourceType,
    NotificationType as NotificationTypeEnum,
)
from app.services.v1.handle_chatwoot.chatbot import (
    coerce_assignee_id,
    extract_assignee_id,
    is_bot_assignee,
)
from app.services.v1.handle_notification import NotificationService, NotificationType

logger = logging.getLogger(__name__)


async def resolve_agent_local_uuid(
    db: AsyncSession,
    tenant_id: UUID,
    chatwoot_agent_id: int | None,
    cw_map: dict[int, UUID] | None = None,
) -> UUID | None:
    """
    Phân giải Chatwoot Agent ID sang Local User UUID với cơ chế dự phòng 3 lớp:
    1. Lớp 1 (Fast in-memory): Tra cứu từ dict cw_map nếu có sẵn.
    2. Lớp 2 (DB Scoped): Truy vấn ChatwootLegacyMap theo đúng tenant_id và chatwoot_id.
    3. Lớp 3 (DB Global fallback): Truy vấn ChatwootLegacyMap chỉ theo chatwoot_id.
    """
    if chatwoot_agent_id is None:
        return None

    try:
        aid_int = int(chatwoot_agent_id)
    except (TypeError, ValueError):
        return None

    # Lớp 1: Tra cứu bộ nhớ tạm
    if cw_map and aid_int in cw_map:
        return cw_map[aid_int]

    # Lớp 2: Truy vấn theo Tenant
    try:
        q = await db.execute(
            select(ChatwootLegacyMap.local_uuid).where(
                and_(
                    ChatwootLegacyMap.resource_type == ChatwootMapResourceType.AGENT,
                    ChatwootLegacyMap.chatwoot_id == aid_int,
                    ChatwootLegacyMap.tenant_id == tenant_id,
                )
            )
        )
        local_uuid = q.scalar_one_or_none()
        if local_uuid:
            return local_uuid
    except Exception as e:
        logger.warning("Lỗi truy vấn ChatwootLegacyMap theo tenant %s: %s", tenant_id, e)

    # Lớp 3: Fallback truy vấn toàn cục nếu record cũ chưa gán tenant_id
    try:
        q_fallback = await db.execute(
            select(ChatwootLegacyMap.local_uuid).where(
                and_(
                    ChatwootLegacyMap.resource_type == ChatwootMapResourceType.AGENT,
                    ChatwootLegacyMap.chatwoot_id == aid_int,
                )
            )
        )
        return q_fallback.scalar_one_or_none()
    except Exception as e:
        logger.warning("Lỗi truy vấn fallback ChatwootLegacyMap: %s", e)
        return None


def extract_sender_display_name(payload: dict[str, Any]) -> str:
    """
    Trích xuất tên người gửi với fallback nhiều cấp:
    sender.name -> conversation.meta.sender.name -> conversation.contact.name -> 'Khách hàng'
    """
    if not isinstance(payload, dict):
        return "Khách hàng"

    sender = payload.get("sender")
    if isinstance(sender, dict) and sender.get("name"):
        return str(sender["name"]).strip()

    conv = payload.get("conversation")
    if isinstance(conv, dict):
        meta = conv.get("meta")
        if isinstance(meta, dict):
            m_sender = meta.get("sender")
            if isinstance(m_sender, dict) and m_sender.get("name"):
                return str(m_sender["name"]).strip()

        contact = conv.get("contact")
        if isinstance(contact, dict) and contact.get("name"):
            return str(contact["name"]).strip()

    return "Khách hàng"


def extract_message_preview(payload: dict[str, Any], max_len: int = 120) -> str:
    """
    Trích xuất nội dung xem trước tin nhắn, có fallback cho tệp đính kèm.
    """
    if not isinstance(payload, dict):
        return "Tin nhắn mới"

    content = payload.get("content")
    if content and isinstance(content, str) and content.strip():
        txt = content.strip()
        return (txt[:max_len] + "...") if len(txt) > max_len else txt

    attachments = payload.get("attachments")
    if isinstance(attachments, list) and attachments:
        first_att = attachments[0] if isinstance(attachments[0], dict) else {}
        file_type = first_att.get("file_type") or first_att.get("data_type")
        if file_type == "image":
            return "[Hình ảnh]"
        elif file_type == "audio":
            return "[Tin nhắn thoại]"
        elif file_type == "video":
            return "[Video]"
        return "[Tệp đính kèm]"

    return "Tin nhắn mới"


async def notify_chatwoot_incoming_message(
    db: AsyncSession,
    tenant_id: UUID,
    payload: dict[str, Any],
    cw_map: dict[int, UUID] | None = None,
) -> bool:
    """
    Lưu và gửi thông báo khi có tin nhắn mới từ khách hàng tới Agent phụ trách.
    Bảo đảm cách ly lỗi (error isolation) để không ảnh hưởng luồng webhook chính.
    """
    try:
        conversation_payload = payload.get("conversation") or {}
        if not isinstance(conversation_payload, dict):
            conversation_payload = {}

        conversation_id = conversation_payload.get("id") or payload.get("conversation_id")
        inbox_id = conversation_payload.get("inbox_id") or payload.get("inbox_id")
        message_id = payload.get("id")

        # 1. Trích xuất Assignee
        aid = extract_assignee_id(conversation_payload) or extract_assignee_id(payload)
        if aid is None:
            logger.debug(
                "Cuộc trò chuyện conv=%s chưa được gán agent, bỏ qua thông báo cá nhân",
                conversation_id,
            )
            return False

        # 2. Bỏ qua nếu người phụ trách là AI Bot
        if await is_bot_assignee(db, tenant_id, aid):
            logger.debug(
                "Assignee %s của conv=%s là AI Bot, không gửi thông báo cho Agent người",
                aid,
                conversation_id,
            )
            return False

        # 3. Phân giải sang Local User UUID
        agent_uuid = await resolve_agent_local_uuid(db, tenant_id, aid, cw_map)
        if not agent_uuid:
            logger.warning(
                "Không tìm thấy Local User UUID tương ứng cho Chatwoot Agent ID %s (tenant=%s)",
                aid,
                tenant_id,
            )
            return False

        # 4. Chuẩn bị nội dung
        sender_name = extract_sender_display_name(payload)
        message_preview = extract_message_preview(payload)

        title = f"Tin nhắn mới từ {sender_name}"
        data = {
            "type": "chatwoot_message",
            "conversation_id": conversation_id,
            "inbox_id": inbox_id,
            "message_id": message_id,
            "sender_name": sender_name,
            "chatwoot_agent_id": aid,
        }

        # 5. Lưu DB & Gửi qua Socket (tự động phân biệt online / offline)
        await NotificationService.send_notification_to_user(
            user_id=agent_uuid,
            title=title,
            message=message_preview,
            notification_type=NotificationType.INFO,
            data=data,
            db=db,
        )
        logger.info(
            "Đã tạo thông báo tin nhắn mới tới user %s (conv=%s, sender=%s)",
            agent_uuid,
            conversation_id,
            sender_name,
        )
        return True

    except Exception as exc:
        logger.warning(
            "Lỗi khi xử lý thông báo tin nhắn Chatwoot (đã cách ly an toàn): %s",
            exc,
            exc_info=True,
        )
        return False


async def notify_chatwoot_conversation_assignment(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_payload: dict[str, Any],
    assignee_id: int | None,
    cw_map: dict[int, UUID] | None = None,
) -> bool:
    """
    Gửi thông báo khi hội thoại được giao/chuyển giao cho một Agent người.
    """
    try:
        aid = coerce_assignee_id(assignee_id)
        if aid is None:
            return False

        # Bỏ qua nếu là AI Bot
        if await is_bot_assignee(db, tenant_id, aid):
            return False

        agent_uuid = await resolve_agent_local_uuid(db, tenant_id, aid, cw_map)
        if not agent_uuid:
            return False

        conversation_id = conversation_payload.get("id")
        inbox_id = conversation_payload.get("inbox_id")
        sender_name = extract_sender_display_name({"conversation": conversation_payload})

        title = "Hội thoại mới được phân công"
        message = f"Bạn vừa được giao phụ trách hội thoại #{conversation_id} từ {sender_name}."
        data = {
            "type": "conversation_assigned",
            "conversation_id": conversation_id,
            "inbox_id": inbox_id,
            "sender_name": sender_name,
            "chatwoot_agent_id": aid,
        }

        await NotificationService.send_notification_to_user(
            user_id=agent_uuid,
            title=title,
            message=message,
            notification_type=NotificationType.INFO,
            data=data,
            db=db,
        )
        logger.info(
            "Đã thông báo phân công hội thoại conv=%s tới user %s",
            conversation_id,
            agent_uuid,
        )
        return True

    except Exception as exc:
        logger.warning(
            "Lỗi khi gửi thông báo phân công hội thoại (đã cách ly an toàn): %s",
            exc,
            exc_info=True,
        )
        return False
