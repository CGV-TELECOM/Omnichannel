import logging
from typing import Any
from uuid import UUID
import httpx

from app.core.config.app_config import settings
from app.db.models import Tenant
from app.integrations.chatwoot import client as chatwoot_client
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def should_bot_respond(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_payload: dict[str, Any]
) -> bool:
    """
    Quyết định xem chatbot có nên phản hồi khách hàng trong hội thoại này không.
    """
    # 1. Kiểm tra Tenant có cấu hình Graph (KB) hay không
    tenant = await db.get(Tenant, tenant_id)
    if not tenant or not tenant.graph_id or not tenant.graph_activated:
        return False

    # Đọc cấu hình từ tenant meta_data
    tenant_meta = tenant.meta_data if isinstance(tenant.meta_data, dict) else {}
    if tenant_meta.get("chatbot_enabled") is False:
        return False

    # 2. Đọc thông tin hội thoại từ Chatwoot payload
    custom_attrs = conversation_payload.get("custom_attributes") or {}
    labels = conversation_payload.get("labels") or []

    # 3. Ưu tiên kiểm tra Manual override (Label hoặc Custom Attribute)
    if "bot-disabled" in labels or custom_attrs.get("is_bot_active") is False:
        return False
    if "bot-active" in labels or custom_attrs.get("is_bot_active") is True:
        return True

    # 4. Kiểm tra xem hội thoại có nhân viên hỗ trợ (assignee) hay không
    assignee = conversation_payload.get("assignee")
    if assignee is not None:
        assignee_id = assignee.get("id")
        integration_bot_id = settings.CHATWOOT_INTEGRATION_USER_ID

        # Nếu đã gán cho một agent con người (không phải bot) -> Không trả lời
        if assignee_id is not None and assignee_id != integration_bot_id:
            return False

    # 5. Nếu chưa gán hoặc gán cho bot, dùng cấu hình mặc định (default_responder) của Tenant
    # Mặc định là "bot" nếu không khai báo
    default_responder = tenant_meta.get("default_responder", "bot")
    return default_responder == "bot"


async def call_kg_chatbot_core(
    tenant_id: UUID,
    graph_id: UUID,
    session_id: str,
    message_content: str,
) -> str | None:
    """
    Gọi sang API của hệ thống KG Chatbot Core để lấy câu trả lời.
    """
    headers = {
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }
    if settings.KG_CORE_API_KEY:
        headers["Authorization"] = f"Bearer {settings.KG_CORE_API_KEY}"
        headers["x-api-key"] = settings.KG_CORE_API_KEY
        headers["api-key"] = settings.KG_CORE_API_KEY

    payload = {
        "agent_id": str(graph_id),
        "channel": "admin-ui",
        "include_citations": True,
        "messages": [{"role": "user", "content": message_content}],
        "session_id": session_id,
        "stream": True,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                settings.KG_CORE_URL,
                json=payload,
                headers=headers
            ) as response:
                if response.status_code == 401:
                    logger.error("KG Chatbot Core trả về 401 Unauthorized. Hãy kiểm tra lại KG_CORE_API_KEY trong .env.")
                    return None
                response.raise_for_status()

                full_response = ""
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            import json
                            chunk = json.loads(data_str)
                            if isinstance(chunk, dict):
                                choices = chunk.get("choices")
                                if choices and isinstance(choices, list) and len(choices) > 0:
                                    delta = choices[0].get("delta")
                                    if delta and isinstance(delta, dict):
                                        full_response += delta.get("content") or ""
                                else:
                                    full_response += chunk.get("content") or chunk.get("text") or ""
                        except Exception:
                            pass
                
                if full_response:
                    return full_response.strip()
    except Exception as e:
        logger.error(
            "Lỗi khi gọi KG Chatbot Core cho session %s: %s",
            session_id,
            str(e)
        )
    return None


async def send_chatwoot_reply(
    account_id: int,
    conversation_id: int,
    reply_text: str
) -> bool:
    """
    Gửi câu trả lời của Bot sang Chatwoot dưới dạng tin nhắn Outgoing.
    """
    path = f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages"
    payload = {
        "content": reply_text,
        "message_type": "outgoing",
    }
    res = await chatwoot_client.application_request("POST", path, json_body=payload)
    if res.status_code not in (200, 201):
        logger.error(
            "Gửi tin nhắn trả lời Chatwoot thất bại cho hội thoại %s: %s",
            conversation_id,
            res.data
        )
        return False
    return True


async def chatbot_enabled(
    account_id: int,
    conversation_id: int,
    is_active: bool
):
    """
    Cập nhật trạng thái hoạt động của bot lên Custom Attributes và Labels của cuộc hội thoại trên Chatwoot.
    """
    path = f"/api/v1/accounts/{account_id}/conversations/{conversation_id}"
    payload = {
        "custom_attributes": {
            "is_bot_active": is_active
        }
    }
    # Cập nhật custom attributes
    await chatwoot_client.application_request("PUT", path, json_body=payload)

    # Cập nhật Labels tương ứng để dễ nhìn trực quan
    labels_path = f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/labels"
    labels = ["bot-active"] if is_active else ["bot-disabled"]
    await chatwoot_client.application_request("POST", labels_path, json_body={"labels": labels})


async def send_internal_note(
    account_id: int,
    conversation_id: int,
    note_text: str
):
    """
    Tạo tin nhắn ghi chú nội bộ (Internal Note) trong Chatwoot.
    """
    path = f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages"
    payload = {
        "content": note_text,
        "message_type": "outgoing",
        "private": True,  # private=True là cờ để Chatwoot biết đây là Internal Note
    }
    await chatwoot_client.application_request("POST", path, json_body=payload)
