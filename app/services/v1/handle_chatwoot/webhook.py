from __future__ import annotations

import logging
from typing import Any

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
    Xử lý Webhook gửi từ messaging.
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
            logger.warning("Payload webhook của messaging không có account ID: %s", payload)
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
                "Không tìm thấy tenant nội bộ tương ứng với messaging account ID: %s",
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
            "Đang phát sự kiện messaging '%s' tới tenant %s", event_type, tenant_id
        )

        cw_map = await _chatwoot_agent_id_to_local_map(db, tenant_id)
        mapped_payload = _walk_redact_agent_refs(payload, cw_map)

        await socket_manager.send_to_tenant(
            tenant_id=tenant_id,
            event="messaging_event",
            data={"event": event_type, "payload": mapped_payload},
        )

        # 4. Bot control (assignee = nguồn sự thật)
        from app.services.v1.handle_chatwoot.chatbot import (
            claim_and_reply_omnihub_kg,
            coerce_assignee_id,
            extract_assignee_id,
            is_bot_assignee,
            is_incoming_customer_message,
            maybe_auto_assign_ai_bot,
            sync_bot_flags_for_assignee,
        )

        account_id_int = int(account_id)

        # 4a. Conversation mới → auto-assign AI Bot (nếu policy tenant cho phép)
        if event_type == "conversation_created":
            conv = payload
            if "conversation" in payload and isinstance(payload["conversation"], dict):
                conv = payload["conversation"]
            conversation_id = conv.get("id") or payload.get("id")
            if conversation_id is not None:
                ok, detail = await maybe_auto_assign_ai_bot(
                    db,
                    tenant_id=tenant_id,
                    account_id=account_id_int,
                    conversation_id=int(conversation_id),
                    conversation_payload=conv if isinstance(conv, dict) else {},
                )
                logger.info(
                    "Auto-assign AI Bot conv=%s ok=%s detail=%s",
                    conversation_id,
                    ok,
                    detail,
                )

        # 4b. Tin khách → Reply Gate OmniHub KG
        elif event_type == "message_created" and is_incoming_customer_message(payload):
            conversation_payload = payload.get("conversation") or {}
            if not isinstance(conversation_payload, dict):
                conversation_payload = {}
            conversation_id = conversation_payload.get("id") or payload.get(
                "conversation_id"
            )
            message_content = (payload.get("content") or "").strip()
            message_id = payload.get("id")

            if conversation_id and message_content:
                if extract_assignee_id(conversation_payload) is None:
                    await maybe_auto_assign_ai_bot(
                        db,
                        tenant_id=tenant_id,
                        account_id=account_id_int,
                        conversation_id=int(conversation_id),
                        conversation_payload=conversation_payload,
                    )
                    from app.services.v1.handle_chatwoot.chatbot import (
                        fetch_conversation_assignee_id,
                    )

                    aid = await fetch_conversation_assignee_id(
                        account_id_int, int(conversation_id)
                    )
                    if aid is not None:
                        conversation_payload = {
                            **conversation_payload,
                            "assignee": {"id": aid},
                            "assignee_id": aid,
                        }

                await claim_and_reply_omnihub_kg(
                    db,
                    tenant_id=tenant_id,
                    account_id=account_id_int,
                    conversation_id=int(conversation_id),
                    conversation_payload=conversation_payload,
                    message_content=message_content,
                    message_id=message_id,
                )

        # 4c. Assignee đổi → sync bot flags (backup nếu assign ngoài OmniHub)
        elif event_type == "conversation_updated":
            conversation_payload = payload
            if "conversation" in payload and isinstance(payload["conversation"], dict):
                conversation_payload = payload["conversation"]

            conversation_id = conversation_payload.get("id")
            assignee_id = extract_assignee_id(conversation_payload)

            changed = payload.get("changed_attributes") or []
            assignee_changed = False
            if isinstance(changed, list):
                for item in changed:
                    if not isinstance(item, dict):
                        continue
                    if "assignee_id" in item or "assignee" in item:
                        assignee_changed = True
                        break
                    attr = str(item.get("attribute_name") or item.get("name") or "")
                    if attr in ("assignee_id", "assignee"):
                        assignee_changed = True
                        break

            # Chỉ sync khi assignee thực sự đổi — tránh spam label API trên mọi update
            if conversation_id is not None and assignee_changed:
                custom_attrs = conversation_payload.get("custom_attributes") or {}
                labels = conversation_payload.get("labels") or []
                if not isinstance(labels, list):
                    labels = []

                aid = coerce_assignee_id(assignee_id)
                if await is_bot_assignee(db, tenant_id, aid):
                    if (
                        custom_attrs.get("is_bot_active") is not True
                        and "bot-active" not in labels
                    ):
                        await sync_bot_flags_for_assignee(
                            db,
                            tenant_id,
                            account_id_int,
                            int(conversation_id),
                            aid,
                            send_note=False,
                        )
                elif aid is not None:
                    if (
                        custom_attrs.get("is_bot_active") is not False
                        and "bot-disabled" not in labels
                    ):
                        await sync_bot_flags_for_assignee(
                            db,
                            tenant_id,
                            account_id_int,
                            int(conversation_id),
                            aid,
                            send_note=True,
                        )

        # CSAT: chỉ khi status chuyển → resolved
        if event_type in ("conversation_status_changed", "conversation_updated"):
            from app.services.v1.handle_conversation_rating import (
                handle_resolved_conversation_payload,
            )

            await handle_resolved_conversation_payload(
                db,
                tenant_id=tenant_id,
                messaging_account_id=account_id_int,
                payload=payload,
                event_type=event_type,
            )

        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Webhook processed and broadcast successfully",
            {"tenant_id": str(tenant_id), "event": event_type},
        )
    except Exception as e:
        logger.error("Lỗi khi xử lý webhook messaging: %s", str(e))
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Error processing webhook: {str(e)}",
        )
