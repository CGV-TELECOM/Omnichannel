"""
OmniHub AI Bot control.

Nguồn sự thật: assignee trên messaging.
- Assignee ∈ bot ids của **tenant** (resolve từ messaging_bots) → KG được phép trả lời
- Assignee = người / null → bot không trả lời
- Label is_bot_active / bot-active chỉ phụ trợ UI, không thắng assignee người

Bot config theo tenant (meta_data) — nguồn duy nhất:
- messaging_bots: [{ key, agent_uuid, is_default, label }]  (mặc định [])
  Tenant không dùng bot → để []. Thêm phần tử khi bật AI Bot / multi-bot sau này.

Reply idempotent theo Chatwoot message_id (Redis SET NX).

Xem docs/ai_bot_assign_reply_flow.md
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.app_config import settings
from app.core.redis.redis_config import RedisHelper
from app.db.models import Tenant
from app.integrations.chatwoot import client as chatwoot_client
from app.schemas.requests.tenant import MessagingBotEntry
from app.services.v1.handle_chatwoot._shared import (
    _map_tenant_agent_by_local,
    _map_user_by_local,
    _translate_local_agent_uuids_to_remote,
)

logger = logging.getLogger(__name__)

REPLIER_OMNIHUB_KG = "omnihub_kg"
# TTL claim reply theo message_id — chặn webhook retry / duplicate delivery
_BOT_REPLY_IDEMPOTENCY_TTL_SECONDS = 48 * 3600

# Fallback in-process khi Redis không dùng được (single-worker; không thay Redis multi-instance)
_local_reply_claims: dict[str, float] = {}
_LOCAL_CLAIM_MAX = 5000

# Default meta keys cho tenant mới / normalize
DEFAULT_MESSAGING_BOTS: list[dict[str, Any]] = []


def default_tenant_bot_meta() -> dict[str, Any]:
    """Meta chatbot mặc định: có field messaging_bots (rỗng) — không ép dùng bot."""
    return {
        "chatbot_enabled": True,
        "default_responder": "agent",
        "messaging_bots": list(DEFAULT_MESSAGING_BOTS),
    }


def coerce_assignee_id(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = raw.get("id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def extract_assignee_id(payload: dict[str, Any] | None) -> int | None:
    """Lấy assignee id từ conversation / webhook payload (nhiều shape Chatwoot)."""
    if not isinstance(payload, dict):
        return None
    for key in ("assignee_id",):
        aid = coerce_assignee_id(payload.get(key))
        if aid is not None:
            return aid
    for key in ("assignee", "meta"):
        node = payload.get(key)
        if key == "meta" and isinstance(node, dict):
            node = node.get("assignee")
        aid = coerce_assignee_id(node)
        if aid is not None:
            return aid
    conv = payload.get("conversation")
    if isinstance(conv, dict):
        return extract_assignee_id(conv)
    return None


async def fetch_conversation_assignee_id(
    messaging_account_id: int,
    conversation_id: int,
) -> int | None:
    path = f"/api/v1/accounts/{messaging_account_id}/conversations/{conversation_id}"
    res = await chatwoot_client.application_request("GET", path)
    if res.status_code != 200 or not isinstance(res.data, dict):
        logger.warning(
            "Bot control: GET conversation %s thất bại status=%s",
            conversation_id,
            res.status_code,
        )
        return None
    return extract_assignee_id(res.data)


def _parse_uuid(raw: Any) -> UUID | None:
    if raw is None:
        return None
    if isinstance(raw, UUID):
        return raw
    try:
        return UUID(str(raw).strip())
    except (TypeError, ValueError, AttributeError):
        return None


def parse_tenant_messaging_bots(meta: dict[str, Any] | None) -> list[MessagingBotEntry]:
    """Đọc messaging_bots từ meta_data (nguồn sự thật duy nhất)."""
    meta = meta if isinstance(meta, dict) else {}
    entries: list[MessagingBotEntry] = []
    raw_list = meta.get("messaging_bots")
    if isinstance(raw_list, list):
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            agent_uuid = _parse_uuid(item.get("agent_uuid") or item.get("agent_id"))
            if agent_uuid is None:
                continue
            key = str(item.get("key") or "default").strip() or "default"
            label = item.get("label")
            entries.append(
                MessagingBotEntry(
                    key=key[:64],
                    agent_uuid=agent_uuid,
                    is_default=bool(item.get("is_default")),
                    label=(str(label)[:128] if label else None),
                )
            )

    # Legacy one-shot: shorthand cũ → coi như 1 bot default (chỉ khi list trống)
    if not entries:
        shorthand = _parse_uuid(meta.get("messaging_ai_bot_agent_uuid"))
        if shorthand is not None:
            entries.append(
                MessagingBotEntry(
                    key="default",
                    agent_uuid=shorthand,
                    is_default=True,
                    label=None,
                )
            )

    if entries and not any(e.is_default for e in entries):
        entries[0].is_default = True
    elif sum(1 for e in entries if e.is_default) > 1:
        # Chuẩn hóa: chỉ giữ default đầu tiên
        seen = False
        for e in entries:
            if e.is_default:
                if seen:
                    e.is_default = False
                else:
                    seen = True

    return entries


def messaging_bots_to_meta_list(
    entries: list[MessagingBotEntry],
) -> list[dict[str, Any]]:
    return [
        {
            "key": e.key[:64],
            "agent_uuid": str(e.agent_uuid),
            "is_default": bool(e.is_default),
            "label": e.label,
        }
        for e in entries
    ]


def normalize_messaging_bots_meta(meta: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """
    Đảm bảo meta có messaging_bots (list, có thể []), bỏ shorthand legacy.
    Returns (meta_mới, changed).
    """
    meta = dict(meta) if isinstance(meta, dict) else {}
    changed = False

    entries = parse_tenant_messaging_bots(meta)
    serialized = messaging_bots_to_meta_list(entries)

    raw = meta.get("messaging_bots")
    if not isinstance(raw, list) or raw != serialized:
        meta["messaging_bots"] = serialized
        changed = True

    if "messaging_ai_bot_agent_uuid" in meta:
        meta.pop("messaging_ai_bot_agent_uuid", None)
        changed = True

    return meta, changed


def default_bot_agent_uuid(meta: dict[str, Any] | None) -> UUID | None:
    bots = parse_tenant_messaging_bots(meta)
    for e in bots:
        if e.is_default:
            return e.agent_uuid
    return bots[0].agent_uuid if bots else None


def _bot_reply_idempotency_key(account_id: int, message_id: Any) -> str | None:
    if message_id is None:
        return None
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        mid_s = str(message_id).strip()
        if not mid_s:
            return None
        return f"bot:kg_reply:{int(account_id)}:{mid_s}"
    return f"bot:kg_reply:{int(account_id)}:{mid}"


def _local_claim(key: str) -> bool:
    now = time.monotonic()
    ttl = float(_BOT_REPLY_IDEMPOTENCY_TTL_SECONDS)
    # prune expired
    expired = [k for k, ts in _local_reply_claims.items() if now - ts > ttl]
    for k in expired:
        _local_reply_claims.pop(k, None)
    if key in _local_reply_claims and now - _local_reply_claims[key] <= ttl:
        return False
    if len(_local_reply_claims) >= _LOCAL_CLAIM_MAX:
        # drop oldest
        oldest = min(_local_reply_claims, key=_local_reply_claims.get)
        _local_reply_claims.pop(oldest, None)
    _local_reply_claims[key] = now
    return True


def _local_release(key: str) -> None:
    _local_reply_claims.pop(key, None)


async def _claim_incoming_message_for_reply(
    account_id: int,
    message_id: Any,
) -> tuple[bool, str]:
    """
    Claim 1 lần xử lý reply cho message_id (Redis SET NX).
    Không có message_id → cho phép chạy (không dedupe được).
    Redis lỗi → fallback in-process (vẫn reply, tránh chết bot khi misconfig Redis).
    """
    key = _bot_reply_idempotency_key(account_id, message_id)
    if key is None:
        logger.warning(
            "Bot reply thiếu message_id account=%s — bỏ qua idempotency",
            account_id,
        )
        return True, "no_message_id"

    try:
        claimed = await RedisHelper.set_nx(
            key,
            "1",
            expire_seconds=_BOT_REPLY_IDEMPOTENCY_TTL_SECONDS,
        )
    except Exception:
        logger.exception(
            "Bot reply idempotency Redis lỗi account=%s msg=%s — fallback local claim",
            account_id,
            message_id,
        )
        if not _local_claim(key):
            return False, "duplicate_message_local"
        return True, "claimed_local_fallback"

    if not claimed:
        return False, "duplicate_message"
    return True, "claimed"


async def _release_incoming_message_claim(
    account_id: int,
    message_id: Any,
) -> None:
    """Nhả claim khi chưa gửi được reply (kg_empty / send_failed) để webhook retry."""
    key = _bot_reply_idempotency_key(account_id, message_id)
    if key is None:
        return
    _local_release(key)
    try:
        await RedisHelper.delete_key(key)
    except Exception:
        logger.exception(
            "Bot reply release claim thất bại account=%s msg=%s",
            account_id,
            message_id,
        )


async def resolve_tenant_bot_chatwoot_ids(
    db: AsyncSession,
    tenant: Tenant,
) -> set[int]:
    """
    Tập chatwoot agent/user id được coi là bot của tenant.
    Chỉ từ messaging_bots + map UUID.
    Không dùng CHATWOOT_INTEGRATION_USER_ID (tránh nhầm bot giữa tenants).
    """
    meta = tenant.meta_data if isinstance(tenant.meta_data, dict) else {}
    bots = parse_tenant_messaging_bots(meta)
    ids: set[int] = set()
    if not bots:
        logger.info(
            "Tenant %s messaging_bots rỗng — không có AI Bot",
            tenant.id,
        )
        return ids

    uuids = [e.agent_uuid for e in bots]
    remote, missing = await _translate_local_agent_uuids_to_remote(
        db, tenant.id, uuids
    )
    ids.update(int(x) for x in remote)
    if missing:
        logger.warning(
            "Tenant %s messaging_bots thiếu map agent: %s",
            tenant.id,
            missing,
        )
    if not ids:
        logger.warning(
            "Tenant %s có messaging_bots nhưng resolve ra 0 chatwoot id",
            tenant.id,
        )
    return ids


async def resolve_default_bot_chatwoot_id(
    db: AsyncSession,
    tenant: Tenant,
) -> int | None:
    """Chatwoot id dùng cho auto-assign / assign-bot. None nếu tenant chưa config bot."""
    meta = tenant.meta_data if isinstance(tenant.meta_data, dict) else {}
    default_uuid = default_bot_agent_uuid(meta)
    if default_uuid is None:
        logger.info(
            "Tenant %s messaging_bots rỗng / thiếu is_default — bỏ qua auto-assign/assign-bot",
            tenant.id,
        )
        return None

    m = await _map_tenant_agent_by_local(db, tenant.id, default_uuid)
    if m is not None:
        return int(m.chatwoot_id)
    um = await _map_user_by_local(db, default_uuid)
    if um is not None:
        return int(um.chatwoot_id)

    logger.warning(
        "Default bot agent_uuid=%s chưa có map cho tenant %s",
        default_uuid,
        tenant.id,
    )
    return None


async def is_bot_assignee(
    db: AsyncSession,
    tenant: Tenant | UUID,
    assignee_id: Any,
) -> bool:
    aid = coerce_assignee_id(assignee_id)
    if aid is None:
        return False
    if isinstance(tenant, UUID):
        t = await db.get(Tenant, tenant)
        if t is None:
            return False
        tenant = t
    bot_ids = await resolve_tenant_bot_chatwoot_ids(db, tenant)
    return aid in bot_ids


def _tenant_bot_policy(tenant: Tenant) -> tuple[bool, str]:
    """(chatbot_enabled, default_responder)."""
    meta = tenant.meta_data if isinstance(tenant.meta_data, dict) else {}
    enabled = meta.get("chatbot_enabled") is not False
    responder = str(meta.get("default_responder") or "bot").strip().lower()
    if responder not in ("bot", "agent"):
        responder = "bot"
    return enabled, responder


async def should_bot_respond(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_payload: dict[str, Any],
    *,
    messaging_account_id: int | None = None,
    refresh_assignee: bool = True,
) -> tuple[bool, str]:
    """Quyết định bot OmniHub (KG) có được reply không. Returns (allowed, reason)."""
    tenant = await db.get(Tenant, tenant_id)
    if not tenant or not tenant.agent_id or not tenant.graph_activated:
        return False, "tenant_agent_inactive"

    chatbot_enabled_flag, _default_responder = _tenant_bot_policy(tenant)
    if not chatbot_enabled_flag:
        return False, "chatbot_disabled_tenant"

    custom_attrs = conversation_payload.get("custom_attributes") or {}
    labels = conversation_payload.get("labels") or []
    if not isinstance(labels, list):
        labels = []

    if "bot-disabled" in labels or custom_attrs.get("is_bot_active") is False:
        return False, "bot_flag_disabled"

    conversation_id = conversation_payload.get("id")
    assignee_id = extract_assignee_id(conversation_payload)

    if (
        assignee_id is None
        and refresh_assignee
        and messaging_account_id is not None
        and conversation_id is not None
    ):
        assignee_id = await fetch_conversation_assignee_id(
            int(messaging_account_id), int(conversation_id)
        )

    if assignee_id is None:
        return False, "unassigned"

    if not await is_bot_assignee(db, tenant, assignee_id):
        return False, f"human_assignee:{assignee_id}"

    return True, f"bot_assignee:{assignee_id}"


async def sync_bot_flags_for_assignee(
    db: AsyncSession,
    tenant: Tenant | UUID,
    account_id: int,
    conversation_id: int,
    assignee_id: int | None,
    *,
    send_note: bool = True,
) -> str:
    """Đồng bộ label/attr theo assignee (bot ids của tenant)."""
    if await is_bot_assignee(db, tenant, assignee_id):
        await chatbot_enabled(account_id, conversation_id, is_active=True)
        if send_note:
            await send_internal_note(
                account_id,
                conversation_id,
                note_text="AI Bot đang phụ trách. Bot đã được bật lại.",
            )
        return "bot_active"

    await chatbot_enabled(account_id, conversation_id, is_active=False)
    if send_note and assignee_id is not None:
        await send_internal_note(
            account_id,
            conversation_id,
            note_text="Nhân viên hỗ trợ đã tiếp nhận. Bot tự động tạm dừng.",
        )
    return "bot_disabled"


async def assign_to_ai_bot(
    db: AsyncSession,
    tenant: Tenant,
    account_id: int,
    conversation_id: int,
    *,
    sync_flags: bool = True,
    send_note: bool = False,
) -> tuple[bool, str]:
    """Assign conversation cho default AI Bot của tenant (UUID map → chatwoot id)."""
    bot_id = await resolve_default_bot_chatwoot_id(db, tenant)
    if bot_id is None:
        return False, "missing_tenant_bot_agent_uuid"

    path = (
        f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/assignments"
    )
    res = await chatwoot_client.application_request(
        "POST",
        path,
        json_body={"assignee_id": int(bot_id)},
    )
    if res.status_code not in (200, 201):
        logger.warning(
            "Assign AI Bot thất bại conv=%s status=%s data=%s",
            conversation_id,
            res.status_code,
            res.data,
        )
        return False, f"assign_failed:{res.status_code}"

    if sync_flags:
        await sync_bot_flags_for_assignee(
            db,
            tenant,
            account_id,
            conversation_id,
            int(bot_id),
            send_note=send_note,
        )
    return True, "assigned_ai_bot"


async def maybe_auto_assign_ai_bot(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    account_id: int,
    conversation_id: int,
    conversation_payload: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Conversation mới: chatbot_enabled + default_responder=bot → assign default bot."""
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        return False, "tenant_missing"
    enabled, default_responder = _tenant_bot_policy(tenant)
    if not enabled:
        return False, "chatbot_disabled_tenant"
    if default_responder != "bot":
        return False, "default_responder_agent"

    payload = conversation_payload or {}
    assignee_id = extract_assignee_id(payload)
    if assignee_id is None and conversation_id:
        assignee_id = await fetch_conversation_assignee_id(account_id, conversation_id)

    if await is_bot_assignee(db, tenant, assignee_id):
        return False, "already_bot"
    if assignee_id is not None:
        return False, f"already_human:{assignee_id}"

    return await assign_to_ai_bot(
        db,
        tenant,
        account_id,
        conversation_id,
        sync_flags=True,
        send_note=False,
    )


async def claim_and_reply_omnihub_kg(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    account_id: int,
    conversation_id: int,
    conversation_payload: dict[str, Any],
    message_content: str,
    message_id: Any = None,
) -> tuple[bool, str]:
    """Reply Gate (OmniHub KG): chỉ gửi khi assignee vẫn là bot của tenant."""
    claimed, claim_reason = await _claim_incoming_message_for_reply(
        account_id, message_id
    )
    if not claimed:
        logger.info(
            "Bot skip reply conv=%s msg=%s reason=%s",
            conversation_id,
            message_id,
            claim_reason,
        )
        return False, claim_reason

    allowed, reason = await should_bot_respond(
        db,
        tenant_id,
        conversation_payload,
        messaging_account_id=account_id,
        refresh_assignee=True,
    )
    if not allowed:
        logger.info(
            "Bot skip reply conv=%s msg=%s reason=%s",
            conversation_id,
            message_id,
            reason,
        )
        return False, reason

    tenant = await db.get(Tenant, tenant_id)
    if not tenant or not tenant.agent_id:
        return False, "tenant_agent_missing"

    session_id = conversation_payload.get("uuid") or str(conversation_id)
    reply_text = await call_kg_chatbot_core(
        tenant_id=tenant_id,
        agent_id=tenant.agent_id,
        session_id=str(session_id),
        message_content=message_content,
    )
    if not reply_text:
        await _release_incoming_message_claim(account_id, message_id)
        return False, "kg_empty"

    current = await fetch_conversation_assignee_id(account_id, int(conversation_id))
    if not await is_bot_assignee(db, tenant, current):
        logger.info(
            "Bot abort send after KG (assignee changed) conv=%s assignee=%s msg=%s",
            conversation_id,
            current,
            message_id,
        )
        # Giữ claim — webhook retry cùng msg không được gửi lại sau khi đã gọi KG
        return False, f"assignee_changed:{current}"

    ok = await send_chatwoot_reply(
        account_id=account_id,
        conversation_id=int(conversation_id),
        reply_text=reply_text,
    )
    if not ok:
        await _release_incoming_message_claim(account_id, message_id)
        return False, "send_failed"
    return True, "sent"


async def call_kg_chatbot_core(
    tenant_id: UUID,
    agent_id: UUID,
    session_id: str,
    message_content: str,
) -> str | None:
    """Gọi KG Chatbot Core lấy câu trả lời."""
    headers = {
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }
    if settings.KG_CORE_API_KEY:
        headers["Authorization"] = f"Bearer {settings.KG_CORE_API_KEY}"
        headers["x-api-key"] = settings.KG_CORE_API_KEY
        headers["api-key"] = settings.KG_CORE_API_KEY

    payload = {
        "agent_id": str(agent_id),
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
                headers=headers,
            ) as response:
                if response.status_code == 401:
                    logger.error(
                        "KG Chatbot Core 401 — kiểm tra KG_CORE_API_KEY."
                    )
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
                            chunk = json.loads(data_str)
                            if isinstance(chunk, dict):
                                choices = chunk.get("choices")
                                if (
                                    choices
                                    and isinstance(choices, list)
                                    and len(choices) > 0
                                ):
                                    delta = choices[0].get("delta")
                                    if delta and isinstance(delta, dict):
                                        full_response += delta.get("content") or ""
                                else:
                                    full_response += (
                                        chunk.get("content") or chunk.get("text") or ""
                                    )
                        except Exception:
                            pass

                if full_response:
                    return full_response.strip()
    except Exception as e:
        logger.error(
            "Lỗi KG Chatbot Core session %s: %s",
            session_id,
            str(e),
        )
    return None


async def send_chatwoot_reply(
    account_id: int,
    conversation_id: int,
    reply_text: str,
) -> bool:
    """Gửi tin outgoing (bot) lên messaging."""
    path = f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages"
    payload = {
        "content": reply_text,
        "message_type": "outgoing",
    }
    res = await chatwoot_client.application_request("POST", path, json_body=payload)
    if res.status_code not in (200, 201):
        logger.error(
            "Gửi tin bot thất bại conv=%s: %s",
            conversation_id,
            res.data,
        )
        return False
    return True


async def chatbot_enabled(
    account_id: int,
    conversation_id: int,
    is_active: bool,
):
    """Cập nhật is_bot_active + label bot-active / bot-disabled trên messaging."""
    path = f"/api/v1/accounts/{account_id}/conversations/{conversation_id}"
    payload = {"custom_attributes": {"is_bot_active": is_active}}
    await chatwoot_client.application_request("PUT", path, json_body=payload)

    labels_path = (
        f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/labels"
    )
    labels = ["bot-active"] if is_active else ["bot-disabled"]
    await chatwoot_client.application_request(
        "POST", labels_path, json_body={"labels": labels}
    )


async def send_internal_note(
    account_id: int,
    conversation_id: int,
    note_text: str,
):
    """Internal note (private) trên messaging."""
    path = f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages"
    payload = {
        "content": note_text,
        "message_type": "outgoing",
        "private": True,
    }
    await chatwoot_client.application_request("POST", path, json_body=payload)
