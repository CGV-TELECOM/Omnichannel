from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatwootLegacyMap, ChatwootMapResourceType
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)

logger = logging.getLogger(__name__)


async def handle_webhook(payload: dict[str, Any], db: AsyncSession):
    """
    Xử lý Webhook gửi từ Chatwoot.
    Tìm kiếm tenant tương ứng từ account_id và phát tin nhắn qua Socket.IO.
    """
    try:
        # 1. Trích xuất account ID của Chatwoot
        account_id = None
        if "account" in payload and isinstance(payload["account"], dict):
            account_id = payload["account"].get("id")
        if account_id is None:
            account_id = payload.get("account_id")

        if not account_id:
            logger.warning("Payload webhook của Chatwoot không có account ID: %s", payload)
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                "Missing account ID",
            )

        # 2. Truy vấn ánh xạ Tenant tương ứng
        q = await db.execute(
            select(ChatwootLegacyMap).where(
                and_(
                    ChatwootLegacyMap.resource_type == ChatwootMapResourceType.ACCOUNT,
                    ChatwootLegacyMap.chatwoot_id == int(account_id),
                )
            )
        )
        mapping = q.scalar_one_or_none()
        if not mapping:
            logger.warning(
                "Không tìm thấy tenant nội bộ tương ứng với Chatwoot account ID: %s",
                account_id,
            )
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Tenant mapping not found",
            )

        tenant_id = mapping.local_uuid

        # 3. Phát sự kiện real-time thông qua Socket.IO
        from app.core.socket.manager import socket_manager
        from app.services.v1.handle_chatwoot._shared import (
            _chatwoot_agent_id_to_local_map,
            _walk_redact_agent_refs,
        )

        event_type = payload.get("event", "unknown_event")
        logger.info(
            "Đang phát sự kiện Chatwoot '%s' tới tenant %s", event_type, tenant_id
        )

        # Ánh xạ agent ID trong payload sang UUID nội bộ
        cw_map = await _chatwoot_agent_id_to_local_map(db, tenant_id)
        mapped_payload = _walk_redact_agent_refs(payload, cw_map)

        await socket_manager.send_to_tenant(
            tenant_id=tenant_id,
            event="chatwoot_event",
            data={"event": event_type, "payload": mapped_payload},
        )

        # 4. Xử lý logic Chatbot tích hợp
        from app.db.models import Tenant
        from app.core.config.app_config import settings
        from app.services.v1.handle_chatwoot.chatbot import (
            should_bot_respond,
            call_kg_chatbot_core,
            send_chatwoot_reply,
            chatbot_enabled,
            send_internal_note,
        )

        # Xử lý sự kiện tin nhắn mới (message_created)
        if event_type == "message_created" and payload.get("message_type") == "incoming" and not payload.get("private"):
            conversation_payload = payload.get("conversation") or {}
            conversation_id = conversation_payload.get("id")
            message_content = payload.get("content") or ""

            if conversation_id:
                bot_should_reply = await should_bot_respond(db, tenant_id, conversation_payload)
                if bot_should_reply:
                    tenant = await db.get(Tenant, tenant_id)
                    if tenant and tenant.graph_id:
                        session_id = conversation_payload.get("uuid") or str(conversation_id)
                        reply_text = await call_kg_chatbot_core(
                            tenant_id=tenant_id,
                            graph_id=tenant.graph_id,
                            session_id=session_id,
                            message_content=message_content,
                        )
                        if reply_text:
                            await send_chatwoot_reply(
                                account_id=int(account_id),
                                conversation_id=conversation_id,
                                reply_text=reply_text,
                            )

        # Xử lý sự kiện cuộc hội thoại được cập nhật (conversation_updated) để tự động tắt bot
        elif event_type == "conversation_updated":
            conversation_payload = payload
            if "conversation" in payload and isinstance(payload["conversation"], dict):
                conversation_payload = payload["conversation"]

            assignee = conversation_payload.get("assignee")
            conversation_id = conversation_payload.get("id")

            if conversation_id and assignee is not None:
                assignee_id = assignee.get("id")
                integration_bot_id = settings.CHATWOOT_INTEGRATION_USER_ID

                # Nếu được gán cho nhân viên thật (không phải Bot và không phải rỗng)
                if assignee_id is not None and assignee_id != integration_bot_id:
                    custom_attrs = conversation_payload.get("custom_attributes") or {}
                    labels = conversation_payload.get("labels") or []
                    
                    # Nếu bot chưa bị tắt
                    if custom_attrs.get("is_bot_active") is not False and "bot-disabled" not in labels:
                        await chatbot_enabled(
                            account_id=int(account_id),
                            conversation_id=conversation_id,
                            is_active=False,
                        )
                        await send_internal_note(
                            account_id=int(account_id),
                            conversation_id=conversation_id,
                            note_text="Nhân viên hỗ trợ đã tiếp nhận. Bot tự động tạm dừng.",
                        )

        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Webhook processed and broadcast successfully",
            {"tenant_id": str(tenant_id), "event": event_type},
        )
    except Exception as e:
        logger.error("Lỗi khi xử lý webhook Chatwoot: %s", str(e))
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Error processing webhook: {str(e)}",
        )
