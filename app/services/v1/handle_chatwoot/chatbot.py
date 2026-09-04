"""
OmniHub AI Bot control.

Nguồn sự thật: assignee trên messaging.
- Assignee ∈ bot ids của **tenant** (resolve từ messaging_bots) → KG được phép trả lời
- Assignee = người / null → bot không trả lời
- Không có KG agent active → không auto-assign bot (chat thuần người)

Bot config theo tenant (meta_data):
- messaging_bots: [{ key, agent_uuid, is_default, label, tenant_kg_agent_id? }]
- chatbot_enabled / default_responder

KG persona (tenant_kg_agents):
- Sticky trên conversation.custom_attributes: kg_agent_id, tenant_kg_agent_id,
  kg_persona_key, kg_persona_pending
- Live chat (≥2 persona): gửi menu chọn; kênh khác / 1 persona: auto default
- Resolve order: sticky → messaging_bot link → tenant/inbox default

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

# Sticky persona trên conversation.custom_attributes
KG_ATTR_AGENT_ID = "kg_agent_id"
KG_ATTR_ROW_ID = "tenant_kg_agent_id"
KG_ATTR_PERSONA_KEY = "kg_persona_key"
KG_ATTR_PENDING = "kg_persona_pending"


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
    # message_created: đôi khi assignee_id nằm trong messages[].conversation
    messages = payload.get("messages")
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            nested = msg.get("conversation")
            if isinstance(nested, dict):
                aid = coerce_assignee_id(nested.get("assignee_id"))
                if aid is not None:
                    return aid
                aid = extract_assignee_id(nested)
                if aid is not None:
                    return aid
    conv = payload.get("conversation")
    if isinstance(conv, dict):
        return extract_assignee_id(conv)
    return None


def is_incoming_customer_message(payload: dict[str, Any] | None) -> bool:
    """Chatwoot gửi message_type là 'incoming' hoặc 0; bỏ private."""
    if not isinstance(payload, dict):
        return False
    if payload.get("private"):
        return False
    mt = payload.get("message_type")
    if mt in (0, "0", "incoming", "Incoming"):
        return True
    if isinstance(mt, str) and mt.strip().lower() == "incoming":
        return True
    return False


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
            kg_row_id = _parse_uuid(item.get("tenant_kg_agent_id"))
            entries.append(
                MessagingBotEntry(
                    key=key[:64],
                    agent_uuid=agent_uuid,
                    is_default=bool(item.get("is_default")),
                    label=(str(label)[:128] if label else None),
                    tenant_kg_agent_id=kg_row_id,
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
            "tenant_kg_agent_id": (
                str(e.tenant_kg_agent_id) if e.tenant_kg_agent_id else None
            ),
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


async def resolve_kg_agent_id_for_assignee(
    db: AsyncSession,
    tenant: Tenant,
    assignee_chatwoot_id: int | None,
) -> UUID | None:
    """Legacy helper — ưu tiên bot messaging gắn KG, rồi default."""
    result = await resolve_kg_agent_for_reply(
        db,
        tenant,
        conversation_payload=None,
        assignee_chatwoot_id=assignee_chatwoot_id,
        inbox_id=None,
    )
    return result


async def resolve_kg_agent_for_reply(
    db: AsyncSession,
    tenant: Tenant | None,
    *,
    conversation_payload: dict[str, Any] | None,
    assignee_chatwoot_id: int | None,
    inbox_id: int | None = None,
) -> UUID | None:
    """
    Thứ tự resolve kg_agent_id cho KG_CORE_URL:
      1. conversation.custom_attributes.kg_agent_id / tenant_kg_agent_id
      2. default trong scope inbox (tenant_kg_agents)
      3. messaging_bots[assignee].tenant_kg_agent_id
      4. tenant default kg agent
      5. None → không gọi KG
    """
    from app.services.v1.handle_tenant_kg_agent import (
        load_active_kg_personas,
        resolve_default_kg_agent_id,
        resolve_kg_agent_id_by_row_id,
    )

    if tenant is None:
        return None

    attrs = extract_conversation_custom_attributes(conversation_payload)
    sticky = _parse_uuid(attrs.get(KG_ATTR_AGENT_ID))
    if sticky is not None:
        # Validate còn active (tránh sticky trỏ agent đã tắt)
        personas = await load_active_kg_personas(
            db, tenant.id, inbox_id=inbox_id
        )
        if any(p.kg_agent_id == sticky for p in personas):
            return sticky
        # fallback: vẫn cho phép nếu đúng tenant active bất kỳ
        all_personas = await load_active_kg_personas(db, tenant.id)
        if any(p.kg_agent_id == sticky for p in all_personas):
            return sticky

    row_id = _parse_uuid(attrs.get(KG_ATTR_ROW_ID))
    if row_id is not None:
        kg_id = await resolve_kg_agent_id_by_row_id(db, tenant.id, row_id)
        if kg_id is not None:
            return kg_id

    # Pending picker → chưa được phép gọi KG
    if attrs.get(KG_ATTR_PENDING) is True or str(attrs.get(KG_ATTR_PENDING)).lower() in (
        "true",
        "1",
    ):
        return None

    # messaging bot → tenant_kg_agent_id
    meta = tenant.meta_data if isinstance(tenant.meta_data, dict) else {}
    bots = parse_tenant_messaging_bots(meta)
    if assignee_chatwoot_id is not None and bots:
        uuids = [e.agent_uuid for e in bots]
        remote, _ = await _translate_local_agent_uuids_to_remote(
            db, tenant.id, uuids
        )
        cw_to_entry: dict[int, MessagingBotEntry] = {}
        for idx, agent_uuid in enumerate(uuids):
            if idx < len(remote):
                cw_to_entry[int(remote[idx])] = bots[idx]
        entry = cw_to_entry.get(int(assignee_chatwoot_id))
        if entry and entry.tenant_kg_agent_id:
            kg_id = await resolve_kg_agent_id_by_row_id(
                db, tenant.id, entry.tenant_kg_agent_id
            )
            if kg_id is not None:
                return kg_id

    # Default theo inbox scope (null inbox_id trên row = mọi kênh)
    return await resolve_default_kg_agent_id(db, tenant.id, inbox_id=inbox_id)


def extract_conversation_custom_attributes(
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    attrs = payload.get("custom_attributes")
    if isinstance(attrs, dict):
        return dict(attrs)
    conv = payload.get("conversation")
    if isinstance(conv, dict):
        nested = conv.get("custom_attributes")
        if isinstance(nested, dict):
            return dict(nested)
    return {}


def _merge_attr_dicts(*dicts: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for d in dicts:
        if isinstance(d, dict):
            out.update(d)
    return out


def extract_visitor_persona_hints(
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Gộp custom_attributes từ conversation / contact / meta / additional_attributes.
    FE overlay set: omnihub_persona_selection và/hoặc tenant_kg_agent_id.
    """
    if not isinstance(payload, dict):
        return {}
    chunks: list[Any] = [
        payload.get("custom_attributes"),
        payload.get("additional_attributes"),
    ]
    meta = payload.get("meta")
    if isinstance(meta, dict):
        chunks.append(meta.get("custom_attributes"))
        sender = meta.get("sender")
        if isinstance(sender, dict):
            chunks.append(sender.get("custom_attributes"))
            chunks.append(sender.get("additional_attributes"))
    contact = payload.get("contact")
    if isinstance(contact, dict):
        chunks.append(contact.get("custom_attributes"))
        chunks.append(contact.get("additional_attributes"))
    conv = payload.get("conversation")
    if isinstance(conv, dict) and conv is not payload:
        chunks.append(extract_visitor_persona_hints(conv))
    return _merge_attr_dicts(*chunks)


def extract_inbox_id_from_payload(payload: dict[str, Any] | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in ("inbox_id",):
        raw = payload.get(key)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    inbox = payload.get("inbox")
    if isinstance(inbox, dict) and inbox.get("id") is not None:
        try:
            return int(inbox["id"])
        except (TypeError, ValueError):
            pass
    conv = payload.get("conversation")
    if isinstance(conv, dict):
        return extract_inbox_id_from_payload(conv)
    return None


def is_web_widget_channel(payload: dict[str, Any] | None) -> bool:
    """Live chat / website widget — kênh duy nhất hiện picker persona."""
    if not isinstance(payload, dict):
        return False

    def _match(raw: Any) -> bool:
        if not raw:
            return False
        s = str(raw).lower().replace(" ", "")
        return (
            "webwidget" in s
            or "web_widget" in s
            or s in ("website", "channel::webwidget", "channel::website")
        )

    for key in ("channel", "channel_type"):
        if _match(payload.get(key)):
            return True
    inbox = payload.get("inbox")
    if isinstance(inbox, dict):
        if _match(inbox.get("channel_type")) or _match(inbox.get("channel")):
            return True
    meta = payload.get("meta")
    if isinstance(meta, dict) and _match(meta.get("channel")):
        return True
    conv = payload.get("conversation")
    if isinstance(conv, dict) and conv is not payload:
        return is_web_widget_channel(conv)
    return False


async def set_conversation_kg_persona_attrs(
    account_id: int,
    conversation_id: int,
    *,
    kg_agent_id: UUID | None = None,
    tenant_kg_agent_id: UUID | None = None,
    persona_key: str | None = None,
    pending: bool | None = None,
) -> bool:
    """Ghi sticky persona vào custom_attributes (merge qua POST)."""
    attrs: dict[str, Any] = {}
    if kg_agent_id is not None:
        attrs[KG_ATTR_AGENT_ID] = str(kg_agent_id)
    if tenant_kg_agent_id is not None:
        attrs[KG_ATTR_ROW_ID] = str(tenant_kg_agent_id)
    if persona_key is not None:
        attrs[KG_ATTR_PERSONA_KEY] = persona_key
    if pending is not None:
        attrs[KG_ATTR_PENDING] = bool(pending)
    if not attrs:
        return True
    path = (
        f"/api/v1/accounts/{account_id}/conversations/"
        f"{conversation_id}/custom_attributes"
    )
    res = await chatwoot_client.application_request(
        "POST",
        path,
        json_body={"custom_attributes": attrs},
    )
    if res.status_code not in (200, 201):
        # Fallback PUT conversation
        res2 = await chatwoot_client.application_request(
            "PUT",
            f"/api/v1/accounts/{account_id}/conversations/{conversation_id}",
            json_body={"custom_attributes": attrs},
        )
        if res2.status_code not in (200, 201):
            logger.warning(
                "Set kg persona attrs thất bại conv=%s status=%s/%s",
                conversation_id,
                res.status_code,
                res2.status_code,
            )
            return False
        return True
    return True


async def fetch_conversation_custom_attributes(
    account_id: int,
    conversation_id: int,
) -> dict[str, Any]:
    """GET conversation để lấy custom_attributes mới nhất (sau khi set pending)."""
    path = f"/api/v1/accounts/{account_id}/conversations/{conversation_id}"
    res = await chatwoot_client.application_request("GET", path)
    if res.status_code != 200 or not isinstance(res.data, dict):
        return {}
    return extract_conversation_custom_attributes(res.data)


async def maybe_handle_persona_selection(
    db: AsyncSession,
    *,
    tenant: Tenant,
    account_id: int,
    conversation_id: int,
    conversation_payload: dict[str, Any],
    message_content: str,
    inbox_id: int | None,
) -> tuple[bool, str]:
    """
    Nếu đang chờ chọn persona (live chat ≥2): khớp tin → sticky + ack.
    Returns (handled, reason). handled=True → webhook không gọi KG với tin chọn.
    Menu / labels luôn lấy từ tenant_kg_agents (DB), không hardcode.
    """
    from app.services.v1.handle_tenant_kg_agent import load_active_kg_personas

    attrs = extract_conversation_custom_attributes(conversation_payload)
    # Refresh nếu webhook chưa có pending (set ở conversation_created trước đó)
    if KG_ATTR_PENDING not in attrs and KG_ATTR_AGENT_ID not in attrs:
        fresh = await fetch_conversation_custom_attributes(
            account_id, int(conversation_id)
        )
        if fresh:
            attrs = {**attrs, **fresh}
            conversation_payload["custom_attributes"] = attrs

    pending = attrs.get(KG_ATTR_PENDING) is True or str(
        attrs.get(KG_ATTR_PENDING)
    ).lower() in ("true", "1")
    already = _parse_uuid(attrs.get(KG_ATTR_AGENT_ID))
    row_sticky = _parse_uuid(attrs.get(KG_ATTR_ROW_ID))
    if (already is not None or row_sticky is not None) and not pending:
        return False, "persona_already_set"

    personas = await load_active_kg_personas(db, tenant.id, inbox_id=inbox_id)
    if len(personas) < 2:
        return False, "no_picker_needed"

    # Live chat ≥2 chưa sticky: coi như đang chờ chọn (kể cả race pending)
    web = is_web_widget_channel(conversation_payload)
    if not pending and already is None and row_sticky is None and web:
        pending = True
    if not pending:
        return False, "not_pending"

    chosen = match_persona_from_message(message_content, personas)
    if chosen is None:
        # Đang chờ chọn: không gọi KG. Chỉ nhắc lại menu nếu tin giống lựa chọn sai.
        text = (message_content or "").strip()
        looks_like_choice = bool(
            text
            and (
                text.isdigit()
                or text.lower().startswith(("1.", "2.", "3.", "4.", "5."))
                or len(text) <= 64
                and any(
                    str(getattr(p, "key", "") or "").lower() in text.lower()
                    or str(getattr(p, "label", "") or "").lower() in text.lower()
                    for p in personas
                )
            )
        )
        if looks_like_choice:
            await send_persona_picker_message(
                account_id,
                conversation_id,
                personas,
                greeting="Mình chưa nhận ra lựa chọn. Vui lòng chọn lại:",
            )
            return True, "persona_invalid_choice"
        return True, "persona_awaiting_choice"

    ok = await set_conversation_kg_persona_attrs(
        account_id,
        conversation_id,
        kg_agent_id=chosen.kg_agent_id,
        tenant_kg_agent_id=chosen.id,
        persona_key=chosen.key,
        pending=False,
    )
    label = chosen.label or chosen.key
    await send_chatwoot_reply(
        account_id,
        conversation_id,
        f"Đã chọn: {label}. Bạn có thể bắt đầu trò chuyện.",
    )
    ca = dict(attrs)
    ca[KG_ATTR_AGENT_ID] = str(chosen.kg_agent_id)
    ca[KG_ATTR_ROW_ID] = str(chosen.id)
    ca[KG_ATTR_PERSONA_KEY] = chosen.key
    ca[KG_ATTR_PENDING] = False
    conversation_payload["custom_attributes"] = ca
    return True, "persona_selected" if ok else "persona_selected_attr_failed"


async def after_bot_assigned_setup_persona(
    db: AsyncSession,
    *,
    tenant: Tenant,
    account_id: int,
    conversation_id: int,
    conversation_payload: dict[str, Any],
) -> str:
    """
    Sau khi assign AI Bot — luôn có đường ra an toàn:
    preselect Redis → row attr → auto default → in-chat picker.
    Lỗi Redis / Chatwoot message không làm crash webhook.
    """
    from app.services.v1.handle_live_chat_public import (
        PERSONA_ROW_ATTR_KEY,
        resolve_preselected_persona_from_conversation,
    )
    from app.services.v1.handle_tenant_kg_agent import (
        load_active_kg_personas,
        resolve_default_kg_agent_row,
        resolve_kg_agent_id_by_row_id,
    )

    try:
        inbox_id = extract_inbox_id_from_payload(conversation_payload)
        personas = await load_active_kg_personas(db, tenant.id, inbox_id=inbox_id)
        if not personas:
            return "no_personas"

        conv_attrs = extract_conversation_custom_attributes(conversation_payload)

        # 1) Redis / client_session preselect
        try:
            pre = await resolve_preselected_persona_from_conversation(
                db,
                tenant_id=tenant.id,
                inbox_id=inbox_id,
                conversation_payload=conversation_payload,
            )
        except Exception:
            logger.exception(
                "Preselect resolve exception conv=%s — tiếp tục fallback",
                conversation_id,
            )
            pre = None

        if pre:
            kg_id = _parse_uuid(pre.get("kg_agent_id"))
            row_id = _parse_uuid(pre.get("tenant_kg_agent_id"))
            pkey = pre.get("persona_key")
            if kg_id and row_id:
                ok = await set_conversation_kg_persona_attrs(
                    account_id,
                    conversation_id,
                    kg_agent_id=kg_id,
                    tenant_kg_agent_id=row_id,
                    persona_key=str(pkey) if pkey else None,
                    pending=False,
                )
                if ok:
                    ca = dict(conv_attrs)
                    ca[KG_ATTR_AGENT_ID] = str(kg_id)
                    ca[KG_ATTR_ROW_ID] = str(row_id)
                    ca[KG_ATTR_PENDING] = False
                    if pkey:
                        ca[KG_ATTR_PERSONA_KEY] = str(pkey)
                    # Optional: copy meta mở rộng (không đè key hệ thống)
                    meta = pre.get("meta")
                    if isinstance(meta, dict) and meta:
                        for mk, mv in meta.items():
                            sk = str(mk)
                            if sk.startswith("kg_") or sk in (
                                KG_ATTR_AGENT_ID,
                                KG_ATTR_ROW_ID,
                                KG_ATTR_PENDING,
                                KG_ATTR_PERSONA_KEY,
                            ):
                                continue
                            ca[f"oh_meta_{sk}"] = mv
                    conversation_payload["custom_attributes"] = ca
                    return "persona_preselected_session"
                logger.warning(
                    "Sticky preselect thất bại conv=%s — fallback picker/default",
                    conversation_id,
                )

        # 2) tenant_kg_agent_id trên attrs (hiếm)
        hints = extract_visitor_persona_hints(conversation_payload)
        hints = {**hints, **conv_attrs}
        row_hint = _parse_uuid(
            hints.get(PERSONA_ROW_ATTR_KEY) or hints.get(KG_ATTR_ROW_ID)
        )
        if row_hint is not None:
            kg_id = await resolve_kg_agent_id_by_row_id(db, tenant.id, row_hint)
            chosen = next((p for p in personas if p.id == row_hint), None)
            if kg_id is not None and chosen is not None:
                await set_conversation_kg_persona_attrs(
                    account_id,
                    conversation_id,
                    kg_agent_id=kg_id,
                    tenant_kg_agent_id=chosen.id,
                    persona_key=chosen.key,
                    pending=False,
                )
                ca = dict(conv_attrs)
                ca[KG_ATTR_AGENT_ID] = str(kg_id)
                ca[KG_ATTR_ROW_ID] = str(chosen.id)
                ca[KG_ATTR_PERSONA_KEY] = chosen.key
                ca[KG_ATTR_PENDING] = False
                conversation_payload["custom_attributes"] = ca
                return "persona_preselected_row"

        # 3) 1 persona hoặc không phải live chat → default
        if len(personas) == 1 or not is_web_widget_channel(conversation_payload):
            row = await resolve_default_kg_agent_row(
                db, tenant.id, inbox_id=inbox_id
            )
            if row is None:
                row = personas[0]
            await set_conversation_kg_persona_attrs(
                account_id,
                conversation_id,
                kg_agent_id=row.kg_agent_id,
                tenant_kg_agent_id=row.id,
                persona_key=row.key,
                pending=False,
            )
            return "persona_auto_default"

        # 4) Fallback cứng: menu trong chat
        await set_conversation_kg_persona_attrs(
            account_id,
            conversation_id,
            pending=True,
        )
        sent = await send_persona_picker_message(
            account_id, conversation_id, personas
        )
        if not sent:
            logger.warning(
                "Gửi persona picker thất bại conv=%s — pending=true, "
                "tin sau vẫn chặn KG đến khi chọn",
                conversation_id,
            )
            return "persona_picker_pending_send_failed"
        return "persona_picker_sent"
    except Exception:
        logger.exception(
            "after_bot_assigned_setup_persona lỗi conv=%s", conversation_id
        )
        return "persona_setup_error"



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
    from app.services.v1.handle_tenant_kg_agent import tenant_has_active_kg_agent

    tenant = await db.get(Tenant, tenant_id)
    if not tenant or not tenant.graph_activated:
        return False, "tenant_agent_inactive"
    if not await tenant_has_active_kg_agent(db, tenant_id):
        return False, "tenant_kg_agent_missing"

    chatbot_enabled_flag, _default_responder = _tenant_bot_policy(tenant)
    if not chatbot_enabled_flag:
        return False, "chatbot_disabled_tenant"

    # Assignee = nguồn sự thật. Label/attr soft (bot-disabled) chỉ UI — không chặn
    # khi conversation đang assign AI Bot (tránh stale flag sau handback).
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
    """Conversation mới: chatbot_enabled + default_responder=bot + có KG → assign bot."""
    from app.services.v1.handle_tenant_kg_agent import tenant_has_active_kg_agent

    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        return False, "tenant_missing"
    enabled, default_responder = _tenant_bot_policy(tenant)
    if not enabled:
        return False, "chatbot_disabled_tenant"
    if default_responder != "bot":
        return False, "default_responder_agent"
    if not await tenant_has_active_kg_agent(db, tenant_id):
        # Không có dịch vụ KG → chat thuần người
        return False, "no_kg_agent_human_only"

    payload = conversation_payload or {}
    assignee_id = extract_assignee_id(payload)
    if assignee_id is None and conversation_id:
        assignee_id = await fetch_conversation_assignee_id(account_id, conversation_id)

    if await is_bot_assignee(db, tenant, assignee_id):
        # Đã bot — vẫn setup persona nếu chưa sticky
        attrs = extract_conversation_custom_attributes(payload)
        if not attrs.get(KG_ATTR_AGENT_ID):
            detail = await after_bot_assigned_setup_persona(
                db,
                tenant=tenant,
                account_id=account_id,
                conversation_id=conversation_id,
                conversation_payload=payload,
            )
            return False, f"already_bot:{detail}"
        return False, "already_bot"
    if assignee_id is not None:
        return False, f"already_human:{assignee_id}"

    ok, detail = await assign_to_ai_bot(
        db,
        tenant,
        account_id,
        conversation_id,
        sync_flags=True,
        send_note=False,
    )
    if not ok:
        return ok, detail

    persona_detail = await after_bot_assigned_setup_persona(
        db,
        tenant=tenant,
        account_id=account_id,
        conversation_id=conversation_id,
        conversation_payload=payload,
    )
    return True, f"{detail}:{persona_detail}"


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
    # Gate trước — không claim Redis cho tin human/unassigned (tránh chặn retry hữu ích)
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
    if not tenant:
        return False, "tenant_missing"

    inbox_id = extract_inbox_id_from_payload(conversation_payload)

    # Live chat: xử lý chọn persona trước khi gọi KG
    handled, sel_reason = await maybe_handle_persona_selection(
        db,
        tenant=tenant,
        account_id=account_id,
        conversation_id=int(conversation_id),
        conversation_payload=conversation_payload,
        message_content=message_content,
        inbox_id=inbox_id,
    )
    if handled:
        logger.info(
            "Bot persona selection conv=%s msg=%s reason=%s",
            conversation_id,
            message_id,
            sel_reason,
        )
        # Claim để webhook retry không gửi lại menu / ack
        await _claim_incoming_message_for_reply(account_id, message_id)
        return False, sel_reason

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

    assignee_id = extract_assignee_id(conversation_payload)
    if assignee_id is None:
        assignee_id = await fetch_conversation_assignee_id(
            account_id, int(conversation_id)
        )
    kg_agent_id = await resolve_kg_agent_for_reply(
        db,
        tenant,
        conversation_payload=conversation_payload,
        assignee_chatwoot_id=coerce_assignee_id(assignee_id),
        inbox_id=inbox_id,
    )
    if not kg_agent_id:
        await _release_incoming_message_claim(account_id, message_id)
        return False, "tenant_kg_agent_missing"

    session_id = conversation_payload.get("uuid") or str(conversation_id)
    reply_text = await call_kg_chatbot_core(
        tenant_id=tenant_id,
        agent_id=kg_agent_id,
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
    *,
    content_type: str | None = None,
    content_attributes: dict[str, Any] | None = None,
) -> bool:
    """Gửi tin outgoing (bot) lên messaging. Hỗ trợ input_select cho live chat."""
    path = f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages"
    payload: dict[str, Any] = {
        "content": reply_text,
        "message_type": "outgoing",
    }
    if content_type:
        payload["content_type"] = content_type
    if content_attributes:
        payload["content_attributes"] = content_attributes
    res = await chatwoot_client.application_request("POST", path, json_body=payload)
    if res.status_code not in (200, 201):
        logger.error(
            "Gửi tin bot thất bại conv=%s: %s",
            conversation_id,
            res.data,
        )
        return False
    return True


async def send_persona_picker_message(
    account_id: int,
    conversation_id: int,
    personas: list[Any],
    *,
    greeting: str | None = None,
) -> bool:
    """
    Menu chọn persona — labels lấy từ DB (không hardcode).
    Ưu tiên Chatwoot input_select (bấm được trên widget); fallback text có số.
    """
    if not personas:
        return False
    greeting_text = (greeting or "").strip() or (
        "Xin chào! Vui lòng chọn một lựa chọn bên dưới để bắt đầu."
    )
    items = []
    for p in personas:
        title = str(getattr(p, "label", None) or getattr(p, "key", None) or "Lựa chọn")
        # value = opaque row id — webhook khớp chắc, không lộ kg_agent_id
        value = str(getattr(p, "id", "") or getattr(p, "key", "") or title)
        items.append({"title": title, "value": value})

    ok = await send_chatwoot_reply(
        account_id,
        conversation_id,
        greeting_text,
        content_type="input_select",
        content_attributes={"items": items},
    )
    if ok:
        return True
    # Fallback: một số inbox/agent không nhận input_select
    return await send_chatwoot_reply(
        account_id,
        conversation_id,
        build_persona_picker_message(personas, greeting=greeting_text),
    )


def extract_persona_choice_text(message_payload: dict[str, Any] | None) -> str:
    """
    Lấy text dùng để khớp persona từ webhook message_created.
    Chatwoot input_select: content_attributes.submitted_values[].value ưu tiên hơn content.
    """
    if not isinstance(message_payload, dict):
        return ""
    attrs = message_payload.get("content_attributes")
    if isinstance(attrs, dict):
        submitted = attrs.get("submitted_values")
        if isinstance(submitted, list):
            for item in submitted:
                if isinstance(item, dict):
                    val = item.get("value")
                    if val is not None and str(val).strip():
                        return str(val).strip()
                    title = item.get("title")
                    if title is not None and str(title).strip():
                        return str(title).strip()
                elif item is not None and str(item).strip():
                    return str(item).strip()
        # một số bản Chatwoot chỉ có selected_values / values
        for key in ("selected_values", "values"):
            raw = attrs.get(key)
            if isinstance(raw, list) and raw:
                first = raw[0]
                if isinstance(first, dict):
                    v = first.get("value") or first.get("title")
                    if v is not None and str(v).strip():
                        return str(v).strip()
                elif first is not None and str(first).strip():
                    return str(first).strip()
    return (message_payload.get("content") or "").strip()


def build_persona_picker_message(
    personas: list[Any],
    *,
    greeting: str | None = None,
) -> str:
    head = (greeting or "").strip() or (
        "Xin chào! Vui lòng chọn một lựa chọn bên dưới để bắt đầu:"
    )
    lines = [head, ""]
    for i, p in enumerate(personas, start=1):
        label = (
            getattr(p, "label", None) or getattr(p, "key", None) or f"Lựa chọn {i}"
        )
        lines.append(f"{i}. {label}")
    lines.append("")
    lines.append("Trả lời bằng số thứ tự hoặc bấm lựa chọn.")
    return "\n".join(lines)


def match_persona_from_message(
    message: str,
    personas: list[Any],
) -> Any | None:
    """Khớp tin khách / input_select value với persona: id, số, key, label."""
    text = (message or "").strip()
    if not text or not personas:
        return None
    # Chatwoot input_select thường gửi value = row id
    for p in personas:
        pid = str(getattr(p, "id", "") or "").strip().lower()
        if pid and text.lower() == pid:
            return p
    if text.isdigit():
        idx = int(text)
        if 1 <= idx <= len(personas):
            return personas[idx - 1]
    lower = text.lower()
    for p in personas:
        key = str(getattr(p, "key", "") or "").strip().lower()
        label = str(getattr(p, "label", "") or "").strip().lower()
        if key and lower == key:
            return p
        if label and lower == label:
            return p
    for i, p in enumerate(personas, start=1):
        label = str(getattr(p, "label", "") or "").strip()
        if label and label.lower() in lower:
            return p
        if lower.startswith(f"{i}.") or lower.startswith(f"{i})"):
            return p
    return None


_BOT_LABELS = frozenset({"bot-active", "bot-disabled"})


def _normalize_label_list(raw: Any) -> list[str]:
    if isinstance(raw, dict):
        raw = raw.get("payload") or raw.get("labels") or raw.get("data") or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
        elif isinstance(item, dict):
            title = item.get("title") or item.get("name") or item.get("label")
            if title:
                out.append(str(title).strip())
    return out


async def chatbot_enabled(
    account_id: int,
    conversation_id: int,
    is_active: bool,
):
    """Cập nhật is_bot_active + merge label bot-active / bot-disabled (không wipe label khác)."""
    path = f"/api/v1/accounts/{account_id}/conversations/{conversation_id}"
    payload = {"custom_attributes": {"is_bot_active": is_active}}
    await chatwoot_client.application_request("PUT", path, json_body=payload)

    labels_path = (
        f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/labels"
    )
    existing: list[str] = []
    try:
        res = await chatwoot_client.application_request("GET", labels_path)
        if res.status_code == 200:
            existing = _normalize_label_list(res.data)
    except Exception:
        logger.exception(
            "GET conversation labels thất bại conv=%s — merge với list trống",
            conversation_id,
        )

    merged = [lab for lab in existing if lab not in _BOT_LABELS]
    merged.append("bot-active" if is_active else "bot-disabled")
    # giữ thứ tự ổn định, bỏ trùng
    seen: set[str] = set()
    unique: list[str] = []
    for lab in merged:
        if lab not in seen:
            seen.add(lab)
            unique.append(lab)

    await chatwoot_client.application_request(
        "POST", labels_path, json_body={"labels": unique}
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
