"""Public live-chat personas (không JWT) — resolve qua website_token.

Overlay (không phụ thuộc setCustomAttributes):

  1. GET personas → label động
  2. client_session_id (prefix oh_) + POST select → Redis TTL (cửa sổ chờ mở widget)
  3. Inject Chatwoot → setUser(client_session_id)
  4. conversation_created → match identifier → sticky KG trên conversation

Fallback cứng:
  - Redis down lúc select → vẫn 200, persisted=false → FE mở widget → menu trong chat
  - Redis down / miss lúc webhook → menu trong chat (input_select)
  - 1 persona / kênh khác → auto default
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.app_config import settings
from app.core.redis.redis_config import RedisHelper
from app.db.models import Tenant
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)
from app.services.v1.handle_messaging_inbox_binding import (
    get_binding_by_tenant_inbox,
    get_binding_by_website_token,
)
from app.services.v1.handle_tenant_kg_agent import (
    load_active_kg_personas,
)

logger = logging.getLogger(__name__)

# Schema version Redis payload — tăng khi đổi shape (đọc vẫn tương thích v1)
PAYLOAD_VERSION = 1

SELECTION_ATTR_KEY = "omnihub_persona_selection"
PERSONA_ROW_ATTR_KEY = "tenant_kg_agent_id"
CLIENT_SESSION_ATTR_KEY = "omnihub_client_session"

_REDIS_TOKEN_PREFIX = "live_chat:persona_sel:v1:"
_REDIS_SESS_PREFIX = "live_chat:persona_sess:v1:"
# Fallback khi FE POST select nhưng chưa/gọi muộn setUser — khớp IP ngắn hạn
_REDIS_IP_PREFIX = "live_chat:persona_ip:v1:"
_IP_FALLBACK_TTL_CAP = 300  # giây; NAT nhiều user → cửa sổ hẹp + consume-once

_CLIENT_SESSION_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_IP_RE = re.compile(r"^[0-9a-fA-F:.%]{3,64}$")
_META_MAX_KEYS = 16
_META_MAX_VALUE_LEN = 256


def normalize_client_ip(raw: str | None) -> str | None:
    """Chuẩn hóa IP từ X-Forwarded-For / request.client / Chatwoot created_at_ip."""
    if not raw:
        return None
    # XFF có thể "client, proxy1, proxy2"
    first = str(raw).split(",")[0].strip()
    if first.startswith("::ffff:"):
        first = first[7:]
    if not first or not _IP_RE.match(first):
        return None
    return first


def _ip_redis_key(website_token: str, client_ip: str) -> str:
    return f"{_REDIS_IP_PREFIX}{website_token.strip()}:{client_ip}"


def _ip_fallback_ttl() -> int:
    return max(60, min(_select_ttl(), _IP_FALLBACK_TTL_CAP))


def _select_ttl() -> int:
    try:
        n = int(settings.LIVE_CHAT_PERSONA_SELECT_TTL_SECONDS)
    except (TypeError, ValueError):
        n = 3600
    return max(60, min(n, 86400 * 7))  # 1 phút … 7 ngày


def _session_prefix() -> str:
    p = (settings.LIVE_CHAT_CLIENT_SESSION_PREFIX or "oh_").strip() or "oh_"
    return p[:16]


def _selection_mode(persona_count: int, *, bot_ready: bool) -> str:
    if not bot_ready or persona_count <= 0:
        return "off"
    if persona_count == 1:
        return "auto"
    return "picker"


def _persona_public(p: Any) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "key": p.key,
        "label": p.label or p.key,
        "is_default": bool(p.is_default),
    }


def canonicalize_client_session_id(raw: str | None) -> str | None:
    """
    Chuẩn hóa identifier cho setUser.
    - Cho phép FE gửi uuid hoặc đã có prefix → luôn trả về có prefix (oh_…).
    """
    s = (raw or "").strip()
    if not s:
        return None
    prefix = _session_prefix()
    if not s.startswith(prefix):
        s = f"{prefix}{s}"
    if len(s) > 128 or not _CLIENT_SESSION_RE.match(s):
        return None
    return s


def _sess_redis_key(website_token: str, client_session_id: str) -> str:
    return f"{_REDIS_SESS_PREFIX}{website_token.strip()}:{client_session_id}"


def _sanitize_meta(meta: Any) -> dict[str, Any]:
    """Meta mở rộng (campaign, locale, …) — giới hạn kích thước."""
    if not isinstance(meta, dict):
        return {}
    out: dict[str, Any] = {}
    for i, (k, v) in enumerate(meta.items()):
        if i >= _META_MAX_KEYS:
            break
        key = str(k).strip()[:64]
        if not key:
            continue
        if v is None or isinstance(v, (bool, int, float)):
            out[key] = v
        else:
            out[key] = str(v)[:_META_MAX_VALUE_LEN]
    return out


def build_selection_payload(
    *,
    tenant_id: UUID,
    chosen: Any,
    website_token: str,
    inbox_id: int,
    client_session_id: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Payload Redis có version — dễ mở rộng field mới."""
    return {
        "v": PAYLOAD_VERSION,
        "tenant_id": str(tenant_id),
        "tenant_kg_agent_id": str(chosen.id),
        "kg_agent_id": str(chosen.kg_agent_id),
        "persona_key": chosen.key,
        "website_token": website_token,
        "inbox_id": int(inbox_id),
        "client_session_id": client_session_id,
        "meta": meta or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_public_personas_by_website_token(
    db: AsyncSession,
    website_token: str,
) -> dict[str, Any]:
    binding = await get_binding_by_website_token(db, website_token)
    if binding is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.NOT_FOUND,
            "Không tìm thấy live chat theo website_token. "
            "Admin hãy mở danh sách inbox (sync bindings) trước.",
        )

    tenant = await db.get(Tenant, binding.tenant_id)
    if tenant is None or tenant.is_active == 0:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.NOT_FOUND,
            "Tenant không tồn tại hoặc đã tắt",
        )

    meta = tenant.meta_data if isinstance(tenant.meta_data, dict) else {}
    chatbot_enabled = meta.get("chatbot_enabled") is not False
    bots = meta.get("messaging_bots") if isinstance(meta.get("messaging_bots"), list) else []
    has_bot = chatbot_enabled and len(bots) > 0 and int(tenant.graph_activated or 0) == 1

    personas = await load_active_kg_personas(
        db, tenant.id, inbox_id=int(binding.inbox_id)
    )
    mode = _selection_mode(len(personas), bot_ready=has_bot)
    ttl = _select_ttl()
    prefix = _session_prefix()

    payload = {
        "selection_mode": mode,
        # Welcome = Chatwoot inbox greeting / pre-chat. Overlay chỉ list persona.
        "greeting": None,
        "inbox_name": binding.inbox_name,
        "website_token": website_token,
        # Tối thiểu cho FE — chi tiết tích hợp nằm ở docs, không nhồi vào response
        "client_session_prefix": prefix,
        "client_session_ttl_seconds": ttl,
        "personas": [_persona_public(p) for p in personas] if mode != "off" else [],
    }
    return api_response(
        ResponseStatus.SUCCESS,
        ResponseStatusCode.OK,
        "Lấy danh sách persona live chat thành công",
        payload,
    )


async def select_public_persona(
    db: AsyncSession,
    website_token: str,
    persona_id: str,
    *,
    client_session_id: str | None = None,
    meta: dict[str, Any] | None = None,
    client_ip: str | None = None,
) -> dict[str, Any]:
    """
    Validate persona + ghi Redis. Redis lỗi → vẫn success với persisted=false
    (FE mở widget; webhook fallback menu trong chat).

    client_ip: fallback khớp webhook khi contact chưa có identifier (setUser miss).
    """
    binding = await get_binding_by_website_token(db, website_token)
    if binding is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.NOT_FOUND,
            "Không tìm thấy live chat theo website_token.",
        )

    tenant = await db.get(Tenant, binding.tenant_id)
    if tenant is None or tenant.is_active == 0:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.NOT_FOUND,
            "Tenant không tồn tại hoặc đã tắt",
        )

    session_id = canonicalize_client_session_id(client_session_id)
    if session_id is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.BAD_REQUEST,
            "client_session_id không hợp lệ (8–128 ký tự). "
            f"Khuyến nghị: '{_session_prefix()}' + uuid. "
            "FE dùng đúng data.client_session_id cho $chatwoot.setUser.",
        )

    try:
        row_uuid = UUID(str(persona_id).strip())
    except (TypeError, ValueError):
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.BAD_REQUEST,
            "persona_id không hợp lệ",
        )

    personas = await load_active_kg_personas(
        db, tenant.id, inbox_id=int(binding.inbox_id)
    )
    chosen = next((p for p in personas if p.id == row_uuid), None)
    if chosen is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.BAD_REQUEST,
            "persona_id không thuộc inbox/website_token này hoặc đã tắt",
        )

    safe_meta = _sanitize_meta(meta)
    redis_payload = build_selection_payload(
        tenant_id=tenant.id,
        chosen=chosen,
        website_token=website_token,
        inbox_id=int(binding.inbox_id),
        client_session_id=session_id,
        meta=safe_meta,
    )
    selection_token = secrets.token_urlsafe(24)
    ttl = _select_ttl()
    ip_norm = normalize_client_ip(client_ip)
    persisted = False
    try:
        blob = json.dumps(redis_payload, ensure_ascii=False)
        await RedisHelper.set_key(
            _sess_redis_key(website_token, session_id),
            blob,
            expire_seconds=ttl,
        )
        await RedisHelper.set_key(
            f"{_REDIS_TOKEN_PREFIX}{selection_token}",
            blob,
            expire_seconds=ttl,
        )
        if ip_norm:
            await RedisHelper.set_key(
                _ip_redis_key(website_token, ip_norm),
                blob,
                expire_seconds=_ip_fallback_ttl(),
            )
        persisted = True
    except Exception:
        logger.exception(
            "Redis lưu persona selection thất bại — fallback in_chat_picker "
            "token=%s session=%s",
            website_token[:8],
            session_id[:12],
        )

    return api_response(
        ResponseStatus.SUCCESS,
        ResponseStatusCode.OK,
        (
            "Đã ghi nhận lựa chọn persona"
            if persisted
            else "Đã xác nhận persona; tạm không lưu Redis — sẽ chọn lại trong chat nếu cần"
        ),
        {
            "client_session_id": session_id,
            "expires_in": ttl if persisted else 0,
            "persisted": persisted,
            "fallback_mode": None if persisted else "in_chat_picker",
            "persona_id": str(chosen.id),
            "persona": _persona_public(chosen),
            # FE: setUser(client_session_id) — không cần custom attributes
        },
    )


async def consume_persona_selection_token(
    selection_token: str,
    *,
    expected_tenant_id: UUID | None = None,
) -> dict[str, Any] | None:
    if not selection_token or not str(selection_token).strip():
        return None
    key = f"{_REDIS_TOKEN_PREFIX}{str(selection_token).strip()}"
    return await _consume_redis_json(key, expected_tenant_id=expected_tenant_id)


async def consume_persona_selection_by_session(
    website_token: str,
    client_session_id: str,
    *,
    expected_tenant_id: UUID | None = None,
) -> dict[str, Any] | None:
    session_id = canonicalize_client_session_id(client_session_id)
    token = (website_token or "").strip()
    if not session_id or not token:
        return None
    # Thử canonical + raw (phòng FE setUser chưa qua canonicalize)
    keys = [_sess_redis_key(token, session_id)]
    raw = (client_session_id or "").strip()
    if raw and raw != session_id:
        keys.append(_sess_redis_key(token, raw))
    for key in keys:
        hit = await _consume_redis_json(key, expected_tenant_id=expected_tenant_id)
        if hit:
            return hit
    return None


async def consume_persona_selection_by_ip(
    website_token: str,
    client_ip: str,
    *,
    expected_tenant_id: UUID | None = None,
) -> dict[str, Any] | None:
    token = (website_token or "").strip()
    ip = normalize_client_ip(client_ip)
    if not token or not ip:
        return None
    return await _consume_redis_json(
        _ip_redis_key(token, ip), expected_tenant_id=expected_tenant_id
    )


def extract_visitor_ip_candidates(payload: dict[str, Any] | None) -> list[str]:
    """IP visitor từ webhook Chatwoot (created_at_ip / browser_info)."""
    if not isinstance(payload, dict):
        return []
    found: list[str] = []

    def _add(raw: Any) -> None:
        ip = normalize_client_ip(str(raw) if raw is not None else None)
        if ip and ip not in found:
            found.append(ip)

    def _from_attrs(obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        aa = obj.get("additional_attributes")
        if isinstance(aa, dict):
            _add(aa.get("created_at_ip"))
            browser = aa.get("browser") or aa.get("browser_info")
            if isinstance(browser, dict):
                _add(browser.get("ip_address") or browser.get("ip"))

    _from_attrs(payload)
    for key in ("contact", "meta", "conversation", "sender"):
        node = payload.get(key)
        if key == "meta" and isinstance(node, dict):
            _from_attrs(node.get("sender"))
            _from_attrs(node.get("contact"))
        else:
            _from_attrs(node)
    return found


async def resolve_preselected_persona_from_conversation(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    inbox_id: int | None,
    conversation_payload: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Tìm preselect từ Redis. Mọi lỗi Redis → None (caller fallback menu).
    Validate lại persona còn active trong DB trước khi trả.
    """
    try:
        website_token: str | None = None
        if inbox_id is not None:
            binding = await get_binding_by_tenant_inbox(
                db, tenant_id, int(inbox_id)
            )
            if binding is not None:
                website_token = binding.website_token

        candidates = extract_client_session_candidates(conversation_payload)
        hit: dict[str, Any] | None = None
        if website_token:
            for sid in candidates:
                hit = await consume_persona_selection_by_session(
                    website_token, sid, expected_tenant_id=tenant_id
                )
                if hit:
                    break
        if hit is None:
            for sid in candidates:
                hit = await consume_persona_selection_token(
                    sid, expected_tenant_id=tenant_id
                )
                if hit:
                    break
        # Overlay POST OK nhưng contact.identifier trống (chưa setUser) → thử IP
        if hit is None and website_token:
            for ip in extract_visitor_ip_candidates(conversation_payload):
                hit = await consume_persona_selection_by_ip(
                    website_token, ip, expected_tenant_id=tenant_id
                )
                if hit:
                    logger.info(
                        "Preselect qua IP fallback token=%s ip=%s "
                        "(contact thiếu identifier — FE nên setUser)",
                        website_token[:8],
                        ip,
                    )
                    # Dọn luôn key session nếu còn (tránh sticky lần sau)
                    sid = hit.get("client_session_id")
                    if sid:
                        try:
                            await RedisHelper.delete_key(
                                _sess_redis_key(website_token, str(sid))
                            )
                        except Exception:
                            pass
                    break
        if hit is None:
            if not candidates:
                logger.info(
                    "Preselect miss: không có identifier/setUser trên payload "
                    "tenant=%s inbox=%s — fallback picker",
                    tenant_id,
                    inbox_id,
                )
            return None

        # Re-validate agent còn active (tránh sticky agent đã tắt)
        row_id_raw = hit.get("tenant_kg_agent_id")
        try:
            row_uuid = UUID(str(row_id_raw))
        except (TypeError, ValueError):
            return None
        personas = await load_active_kg_personas(
            db, tenant_id, inbox_id=inbox_id
        )
        chosen = next((p for p in personas if p.id == row_uuid), None)
        if chosen is None:
            logger.info(
                "Preselect persona không còn active tenant=%s row=%s — fallback",
                tenant_id,
                row_id_raw,
            )
            return None
        # Đồng bộ lại id hiện tại (tránh payload Redis cũ lệch)
        hit["tenant_kg_agent_id"] = str(chosen.id)
        hit["kg_agent_id"] = str(chosen.kg_agent_id)
        hit["persona_key"] = chosen.key
        return hit
    except Exception:
        logger.exception(
            "resolve_preselected_persona thất bại tenant=%s — fallback in-chat",
            tenant_id,
        )
        return None


def extract_client_session_candidates(
    payload: dict[str, Any] | None,
) -> list[str]:
    """Ưu tiên contact.identifier (= setUser), rồi attr omnihub_client_session."""
    if not isinstance(payload, dict):
        return []
    found: list[str] = []

    def _add(raw: Any) -> None:
        if raw is None:
            return
        text = str(raw).strip()
        if not text:
            return
        # Giữ cả raw và canonical để match Redis
        for cand in (text, canonicalize_client_session_id(text)):
            if cand and cand not in found and _CLIENT_SESSION_RE.match(cand):
                found.append(cand)

    def _walk(obj: Any, depth: int = 0) -> None:
        if depth > 4 or not isinstance(obj, dict):
            return
        _add(obj.get("identifier"))
        _add(obj.get("source_id"))
        ca = obj.get("custom_attributes")
        if isinstance(ca, dict):
            _add(ca.get(CLIENT_SESSION_ATTR_KEY))
            _add(ca.get(SELECTION_ATTR_KEY))
        aa = obj.get("additional_attributes")
        if isinstance(aa, dict):
            _add(aa.get(CLIENT_SESSION_ATTR_KEY))
        for key in ("contact", "sender", "meta", "conversation"):
            nested = obj.get(key)
            if isinstance(nested, dict):
                if key == "meta":
                    _walk(nested.get("sender"), depth + 1)
                    _walk(nested.get("contact"), depth + 1)
                else:
                    _walk(nested, depth + 1)

    _walk(payload)
    return found


async def _consume_redis_json(
    key: str,
    *,
    expected_tenant_id: UUID | None = None,
) -> dict[str, Any] | None:
    """GET rồi best-effort DELETE — lỗi delete không làm mất payload đã đọc."""
    try:
        raw = await RedisHelper.get_key(key)
    except Exception:
        logger.exception("Redis GET persona key thất bại key=%s", key)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Redis persona payload JSON lỗi key=%s", key)
        try:
            await RedisHelper.delete_key(key)
        except Exception:
            pass
        return None
    if not isinstance(data, dict):
        return None
    if expected_tenant_id is not None:
        if str(data.get("tenant_id") or "") != str(expected_tenant_id):
            return None
    try:
        await RedisHelper.delete_key(key)
    except Exception:
        logger.warning(
            "Redis DELETE persona key thất bại (vẫn dùng payload) key=%s", key
        )
    return data
