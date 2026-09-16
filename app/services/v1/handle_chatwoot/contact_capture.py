"""Livechat contact capture — policy theo inbox + upsert Contact Chatwoot.

Mục tiêu: hết “Khách truy cập” bằng cách ghi name/email/phone lên Contact messaging.
Nguồn thu thập: pre-chat Chatwoot, overlay public, bot/agent gọi upsert.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis.redis_config import RedisHelper
from app.integrations.chatwoot import client as chatwoot_client

logger = logging.getLogger(__name__)

ContactFieldKey = Literal["name", "phone", "email"]
CaptureMode = Literal["off", "pre_chat", "bot", "pre_chat_or_bot"]

_STANDARD_FIELDS: tuple[ContactFieldKey, ...] = ("name", "phone", "email")
_DEFAULT_LABELS: dict[str, str] = {
    "name": "Họ và tên",
    "phone": "Số điện thoại",
    "email": "Email",
}
# Tên field chuẩn Chatwoot web widget pre-chat
_CHATWOOT_FIELD_META: dict[str, dict[str, str]] = {
    "name": {"name": "fullName", "type": "text"},
    "email": {"name": "emailAddress", "type": "email"},
    "phone": {"name": "phoneNumber", "type": "text"},
}

_REDIS_CONTACT_PREFIX = "livechat:contact:"
_DEFAULT_CONTACT_TTL = 3600
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[+\d][\d\s().-]{6,20}$")
# Chatwoot Contact.phone_number bắt buộc E.164 (+…). Mặc định VN (+84).
_DEFAULT_PHONE_REGION = "VN"
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone_e164(
    raw: str | None,
    *,
    default_region: str = _DEFAULT_PHONE_REGION,
) -> tuple[str | None, str | None]:
    """
    Chuẩn hóa SĐT → E.164 cho Chatwoot.
    Returns (e164_or_none, error_code_or_none).

    VN: ``0834802680`` → ``+84834802680``; ``84834802680`` → ``+84834802680``.
    """
    if raw is None:
        return None, None
    text = re.sub(r"[\s().-]+", "", str(raw).strip())
    if not text:
        return None, None

    if text.startswith("00"):
        text = "+" + text[2:]

    if text.startswith("+"):
        digits = text[1:]
        if digits.isdigit() and _E164_RE.match(f"+{digits}"):
            return f"+{digits}", None
        return None, "phone_invalid"

    if not text.isdigit():
        return None, "phone_invalid"

    region = (default_region or "VN").upper()
    if region == "VN":
        if text.startswith("0") and 9 <= len(text) <= 11:
            return f"+84{text[1:]}", None
        if text.startswith("84") and 10 <= len(text) <= 12:
            return f"+{text}", None
        if 9 <= len(text) <= 10 and text[0] in "35789":
            return f"+84{text}", None

    if 8 <= len(text) <= 15:
        return None, "phone_e164_required"
    return None, "phone_invalid"


@dataclass
class ContactFieldPolicy:
    key: ContactFieldKey
    label: str
    enabled: bool = True
    required: bool = False


@dataclass
class ContactCapturePolicy:
    enabled: bool = False
    mode: CaptureMode = "pre_chat_or_bot"
    message: str = "Vui lòng để lại thông tin để chúng tôi hỗ trợ bạn tốt hơn."
    fields: list[ContactFieldPolicy] = field(default_factory=list)

    def enabled_fields(self) -> list[ContactFieldPolicy]:
        if not self.enabled or self.mode == "off":
            return []
        return [f for f in self.fields if f.enabled]


def default_contact_capture_policy(*, enabled: bool = False) -> ContactCapturePolicy:
    return ContactCapturePolicy(
        enabled=enabled,
        mode="pre_chat_or_bot" if enabled else "off",
        fields=[
            ContactFieldPolicy("name", _DEFAULT_LABELS["name"], True, True),
            ContactFieldPolicy("phone", _DEFAULT_LABELS["phone"], True, True),
            ContactFieldPolicy("email", _DEFAULT_LABELS["email"], True, False),
        ],
    )


def parse_contact_capture_policy(raw: Any) -> ContactCapturePolicy:
    """Parse từ FE body / inbox.pre_chat_form_options / meta."""
    if not isinstance(raw, dict):
        return default_contact_capture_policy(enabled=False)

    # Cho phép FE gửi dạng gọn
    if "fields" in raw or "enabled" in raw or "mode" in raw:
        enabled = bool(raw.get("enabled", False))
        mode_raw = str(raw.get("mode") or ("pre_chat_or_bot" if enabled else "off")).strip()
        mode: CaptureMode
        if mode_raw in ("off", "pre_chat", "bot", "pre_chat_or_bot"):
            mode = mode_raw  # type: ignore[assignment]
        else:
            mode = "pre_chat_or_bot" if enabled else "off"
        if mode == "off":
            enabled = False
        message = str(raw.get("message") or raw.get("pre_chat_message") or "").strip()
        if not message:
            message = default_contact_capture_policy().message

        by_key: dict[str, ContactFieldPolicy] = {
            f.key: f for f in default_contact_capture_policy(enabled=True).fields
        }
        fields_raw = raw.get("fields")
        if isinstance(fields_raw, list):
            # List tường minh → chỉ bật các field được gửi
            for k in list(by_key.keys()):
                base = by_key[k]
                by_key[k] = ContactFieldPolicy(
                    key=base.key,
                    label=base.label,
                    enabled=False,
                    required=False,
                )
            for item in fields_raw:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "").strip().lower()
                if key not in by_key:
                    continue
                base = by_key[key]
                by_key[key] = ContactFieldPolicy(
                    key=base.key,
                    label=str(item.get("label") or base.label).strip() or base.label,
                    enabled=bool(item.get("enabled", True)),
                    required=bool(item.get("required", False)),
                )
        return ContactCapturePolicy(
            enabled=enabled,
            mode=mode,
            message=message,
            fields=[by_key[k] for k in _STANDARD_FIELDS],
        )

    # Parse từ Chatwoot pre_chat_form_options
    return policy_from_chatwoot_pre_chat(
        enabled=bool(raw.get("pre_chat_form_enabled") or raw.get("enabled")),
        options=raw.get("pre_chat_form_options") or raw,
    )


def policy_from_chatwoot_pre_chat(
    *,
    enabled: bool,
    options: Any,
) -> ContactCapturePolicy:
    policy = default_contact_capture_policy(enabled=enabled)
    if not isinstance(options, dict):
        return policy
    msg = str(options.get("pre_chat_message") or "").strip()
    if msg:
        policy.message = msg
    fields_raw = options.get("pre_chat_fields")
    if not isinstance(fields_raw, list):
        return policy

    name_to_key = {
        meta["name"].lower(): key for key, meta in _CHATWOOT_FIELD_META.items()
    }
    # aliases
    name_to_key.update(
        {
            "fullname": "name",
            "namefield": "name",
            "emailfield": "email",
            "email": "email",
            "phonefield": "phone",
            "phone": "phone",
        }
    )
    by_key = {f.key: f for f in policy.fields}
    for item in fields_raw:
        if not isinstance(item, dict):
            continue
        cw_name = str(item.get("name") or "").strip().lower()
        key = name_to_key.get(cw_name)
        if not key or key not in by_key:
            continue
        base = by_key[key]
        by_key[key] = ContactFieldPolicy(
            key=base.key,  # type: ignore[arg-type]
            label=str(item.get("label") or base.label).strip() or base.label,
            enabled=bool(item.get("enabled", True)),
            required=bool(item.get("required", False)),
        )
    policy.fields = [by_key[k] for k in _STANDARD_FIELDS]  # type: ignore[index]
    if enabled and policy.mode == "off":
        policy.mode = "pre_chat_or_bot"
    return policy


def policy_to_public_dict(policy: ContactCapturePolicy) -> dict[str, Any]:
    return {
        "enabled": policy.enabled and policy.mode != "off",
        "mode": policy.mode if policy.enabled else "off",
        "message": policy.message,
        "fields": [
            {
                "key": f.key,
                "label": f.label,
                "enabled": f.enabled,
                "required": f.required,
            }
            for f in policy.fields
        ],
    }


def build_chatwoot_pre_chat_payload(policy: ContactCapturePolicy) -> dict[str, Any]:
    """Sinh pre_chat_form_enabled + pre_chat_form_options (đặt vào ``channel`` khi PATCH).

    Chatwoot ``Channel::WebWidget::EDITABLE_ATTRS`` — pre_chat_* nằm trên channel,
    không phải top-level inbox (top-level bị strong params bỏ qua, vẫn HTTP 200).
    """
    use_pre_chat = policy.enabled and policy.mode in (
        "pre_chat",
        "pre_chat_or_bot",
    )
    fields_out: list[dict[str, Any]] = []
    for f in policy.fields:
        meta = _CHATWOOT_FIELD_META[f.key]
        fields_out.append(
            {
                "name": meta["name"],
                "type": meta["type"],
                "label": f.label,
                "enabled": bool(f.enabled and use_pre_chat),
                "required": bool(f.required and f.enabled and use_pre_chat),
                "field_type": "standard",
                "placeholder": "",
            }
        )
    # Chỉ key Chatwoot permit (omnihub meta → Redis cache, không gửi CW)
    return {
        "pre_chat_form_enabled": use_pre_chat,
        "pre_chat_form_options": {
            "pre_chat_message": policy.message,
            "pre_chat_fields": fields_out,
        },
    }


# Field website widget FE hay gửi top-level nhưng Chatwoot chỉ nhận trong channel.
# Chỉ các key nằm trong Channel::WebWidget::EDITABLE_ATTRS (bỏ key CW không permit).
_WEB_WIDGET_CHANNEL_KEYS: tuple[str, ...] = (
    "website_url",
    "widget_color",
    "welcome_title",
    "welcome_tagline",
    "reply_time",
    "continuity_via_email",
    "hmac_mandatory",
    "allowed_domains",
    "selected_feature_flags",
    "pre_chat_form_enabled",
    "pre_chat_form_options",
)

_WEB_WIDGET_HINT_KEYS: frozenset[str] = frozenset(
    {
        "website_url",
        "website_token",
        "welcome_title",
        "welcome_tagline",
        "widget_color",
        "selected_feature_flags",
        "pre_chat_form_enabled",
        "pre_chat_form_options",
        "allowed_domains",
    }
)


def looks_like_web_widget_payload(payload: dict[str, Any]) -> bool:
    """True nếu body giống cập nhật Website widget (an toàn để hoist → channel)."""
    if not isinstance(payload, dict):
        return False
    channel = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}
    for key in _WEB_WIDGET_HINT_KEYS:
        if key in payload or key in channel:
            return True
    ctype = str(
        payload.get("channel_type")
        or channel.get("type")
        or channel.get("channel_type")
        or ""
    ).lower()
    compact = ctype.replace(" ", "").replace("_", "").replace(":", "")
    return "webwidget" in compact or ctype in ("website", "web_widget")


def hoist_web_widget_fields_into_channel(
    payload: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    """
    Đưa field widget từ top-level → ``channel`` (tránh Chatwoot silent-ignore).

    Chỉ chạy khi payload giống Website (hoặc ``force=True``).
    Trùng key: **giá trị trong ``channel`` thắng**, top-level bị bỏ.
    """
    if not isinstance(payload, dict):
        return payload
    if not force and not looks_like_web_widget_payload(payload):
        # Vẫn bỏ top-level pre_chat_* nếu có (tránh CW ignore nhầm)
        payload.pop("pre_chat_form_enabled", None)
        payload.pop("pre_chat_form_options", None)
        return payload

    channel = payload.get("channel")
    if channel is None:
        channel = {}
    if not isinstance(channel, dict):
        return payload

    moved = False
    for key in _WEB_WIDGET_CHANNEL_KEYS:
        if key in payload and key not in channel:
            channel[key] = payload.pop(key)
            moved = True
        elif key in payload and key in channel:
            # channel thắng; bỏ top-level trùng
            payload.pop(key)
            moved = True
    if moved or channel:
        payload["channel"] = channel
    return payload


def apply_contact_capture_to_inbox_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Nếu body có ``contact_capture`` → ``channel.pre_chat_form_*`` Chatwoot.
    Hoist field widget top-level → ``channel`` chỉ khi giống Website inbox.
    """
    if not isinstance(payload, dict):
        return payload
    raw = payload.pop("contact_capture", None)
    # Có contact_capture → gần như chắc Settings Website; force hoist an toàn
    force_hoist = raw is not None or looks_like_web_widget_payload(payload)
    payload = hoist_web_widget_fields_into_channel(payload, force=force_hoist)
    if raw is None:
        return payload
    policy = parse_contact_capture_policy(raw)
    cw = build_chatwoot_pre_chat_payload(policy)
    channel = payload.get("channel")
    if not isinstance(channel, dict):
        channel = {}
        payload["channel"] = channel
    channel["pre_chat_form_enabled"] = cw["pre_chat_form_enabled"]
    channel["pre_chat_form_options"] = cw["pre_chat_form_options"]
    payload.pop("pre_chat_form_enabled", None)
    payload.pop("pre_chat_form_options", None)
    return payload


def normalize_contact_payload(
    raw: dict[str, Any] | None,
    *,
    policy: ContactCapturePolicy | None = None,
) -> tuple[dict[str, str], list[str]]:
    """
    Chuẩn hóa name/email/phone.
    Phone → E.164 (Chatwoot bắt buộc) khi hợp lệ.
    Returns (cleaned, errors).
    """
    data = raw if isinstance(raw, dict) else {}
    cleaned: dict[str, str] = {}
    errors: list[str] = []

    name = str(data.get("name") or data.get("fullName") or "").strip()
    email = str(data.get("email") or data.get("emailAddress") or "").strip().lower()
    phone_raw = str(data.get("phone") or data.get("phoneNumber") or "").strip()

    if name:
        cleaned["name"] = name[:120]
    if email:
        if not _EMAIL_RE.match(email):
            errors.append("email_invalid")
        else:
            cleaned["email"] = email[:255]
    if phone_raw:
        e164, phone_err = normalize_phone_e164(phone_raw)
        if phone_err or not e164:
            errors.append(phone_err or "phone_invalid")
        else:
            cleaned["phone"] = e164[:20]

    if policy is not None and policy.enabled and policy.mode != "off":
        for f in policy.enabled_fields():
            if f.required and f.key not in cleaned:
                errors.append(f"{f.key}_required")

    return cleaned, errors


def _contact_redis_key(website_token: str, client_session_id: str) -> str:
    return f"{_REDIS_CONTACT_PREFIX}{website_token.strip()}:{client_session_id.strip()}"


async def store_pending_contact(
    *,
    website_token: str,
    client_session_id: str,
    tenant_id: UUID,
    inbox_id: int,
    contact: dict[str, str],
    ttl_seconds: int = _DEFAULT_CONTACT_TTL,
) -> bool:
    payload = {
        "tenant_id": str(tenant_id),
        "inbox_id": int(inbox_id),
        "website_token": website_token,
        "client_session_id": client_session_id,
        "contact": contact,
    }
    try:
        await RedisHelper.set_key(
            _contact_redis_key(website_token, client_session_id),
            json.dumps(payload, ensure_ascii=False),
            expire_seconds=max(60, int(ttl_seconds)),
        )
        return True
    except Exception:
        logger.exception(
            "Redis lưu pending contact thất bại token=%s session=%s",
            website_token,
            client_session_id,
        )
        return False


async def consume_pending_contact(
    *,
    website_token: str | None,
    client_session_ids: list[str],
    expected_tenant_id: UUID | None = None,
) -> dict[str, Any] | None:
    if not website_token or not client_session_ids:
        return None
    for sid in client_session_ids:
        key = _contact_redis_key(website_token, sid)
        try:
            raw = await RedisHelper.get_key(key)
        except Exception:
            logger.exception("Redis GET pending contact thất bại key=%s", key)
            continue
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                await RedisHelper.delete_key(key)
            except Exception:
                pass
            continue
        if not isinstance(data, dict):
            continue
        if expected_tenant_id is not None and str(data.get("tenant_id") or "") != str(
            expected_tenant_id
        ):
            continue
        try:
            await RedisHelper.delete_key(key)
        except Exception:
            pass
        return data
    return None


async def upsert_chatwoot_contact(
    *,
    account_id: int,
    contact_id: int,
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    additional_attributes: dict[str, Any] | None = None,
    access_token: str | None = None,
) -> tuple[bool, int, Any]:
    """PATCH /api/v1/accounts/{account_id}/contacts/{contact_id}."""
    body: dict[str, Any] = {}
    if name:
        body["name"] = name
    if email:
        body["email"] = email
    if phone:
        e164, _err = normalize_phone_e164(phone)
        body["phone_number"] = e164 or phone
    if additional_attributes:
        body["additional_attributes"] = additional_attributes
    if not body:
        return False, 400, {"error": "empty_contact_payload"}

    res = await chatwoot_client.application_request(
        "PATCH",
        f"/api/v1/accounts/{int(account_id)}/contacts/{int(contact_id)}",
        json_body=body,
        access_token=access_token,
    )
    ok = res.status_code in (200, 201)
    # Phone sai format → retry name/email (tránh mất toàn bộ vì 1 field)
    if (
        not ok
        and res.status_code == 422
        and phone
        and ("phone" in str(res.data).lower() or "e164" in str(res.data).lower())
    ):
        logger.warning(
            "Chatwoot reject phone_number=%s — retry không phone account=%s contact=%s",
            phone,
            account_id,
            contact_id,
        )
        body_retry = {k: v for k, v in body.items() if k != "phone_number"}
        if body_retry:
            res = await chatwoot_client.application_request(
                "PATCH",
                f"/api/v1/accounts/{int(account_id)}/contacts/{int(contact_id)}",
                json_body=body_retry,
                access_token=access_token,
            )
            ok = res.status_code in (200, 201)
    if not ok:
        logger.warning(
            "Upsert Chatwoot contact thất bại account=%s contact=%s status=%s",
            account_id,
            contact_id,
            res.status_code,
        )
    return ok, int(res.status_code or 502), res.data


def extract_contact_id_from_payload(payload: dict[str, Any] | None) -> int | None:
    if not isinstance(payload, dict):
        return None

    def _as_id(raw: Any) -> int | None:
        try:
            n = int(raw)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None

    for key in ("contact_id", "id"):
        found = _as_id(payload.get(key))
        if found and key == "contact_id":
            return found

    contact = payload.get("contact")
    if isinstance(contact, dict):
        found = _as_id(contact.get("id"))
        if found:
            return found

    meta = payload.get("meta")
    if isinstance(meta, dict):
        sender = meta.get("sender")
        if isinstance(sender, dict):
            # web widget: sender type contact
            if str(sender.get("type") or "").lower() in ("contact", ""):
                found = _as_id(sender.get("id"))
                if found:
                    return found
        c2 = meta.get("contact")
        if isinstance(c2, dict):
            found = _as_id(c2.get("id"))
            if found:
                return found

    conv = payload.get("conversation")
    if isinstance(conv, dict):
        return extract_contact_id_from_payload(conv)
    return None


def extract_website_token_from_payload(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    for nest in (
        payload,
        payload.get("inbox") if isinstance(payload.get("inbox"), dict) else None,
        payload.get("conversation")
        if isinstance(payload.get("conversation"), dict)
        else None,
    ):
        if not isinstance(nest, dict):
            continue
        for key in ("website_token", "inbox_website_token"):
            tok = nest.get(key)
            if tok and str(tok).strip():
                return str(tok).strip()
        ch = nest.get("channel")
        if isinstance(ch, dict):
            tok = ch.get("website_token") or ch.get("token")
            if tok and str(tok).strip():
                return str(tok).strip()
    return None


def contact_capture_from_inbox_payload(inbox: Any) -> dict[str, Any]:
    """Derive contact_capture public dict từ payload inbox Chatwoot."""
    if not isinstance(inbox, dict):
        return policy_to_public_dict(default_contact_capture_policy(enabled=False))
    channel = inbox.get("channel") if isinstance(inbox.get("channel"), dict) else {}
    enabled = bool(
        inbox.get("pre_chat_form_enabled")
        if inbox.get("pre_chat_form_enabled") is not None
        else channel.get("pre_chat_form_enabled")
    )
    options = (
        inbox.get("pre_chat_form_options")
        or channel.get("pre_chat_form_options")
        or {}
    )
    if not isinstance(options, dict):
        options = {}

    # 1) Top-level contact_capture (đã enrich)
    if isinstance(inbox.get("contact_capture"), dict):
        return policy_to_public_dict(parse_contact_capture_policy(inbox["contact_capture"]))

    # 2) Meta OmniHub trong pre_chat_form_options (giữ mode=bot khi pre_chat tắt)
    embedded = options.get("omnihub_contact_capture")
    if isinstance(embedded, dict):
        return policy_to_public_dict(parse_contact_capture_policy(embedded))

    # 3) Derive thuần từ pre-chat Chatwoot
    policy = policy_from_chatwoot_pre_chat(enabled=enabled, options=options)
    if not enabled:
        policy.enabled = False
        policy.mode = "off"
    elif policy.mode == "off":
        policy.mode = "pre_chat"
    return policy_to_public_dict(policy)


def enrich_inbox_api_result(result: Any) -> Any:
    """Gắn ``contact_capture`` vào data.messaging khi GET/PATCH inbox thành công."""
    if not isinstance(result, dict) or result.get("status") != "success":
        return result
    data = result.get("data")
    if not isinstance(data, dict):
        return result
    messaging = data.get("messaging")
    if not isinstance(messaging, dict):
        return result
    # Chatwoot đôi khi bọc payload
    inbox_obj = messaging.get("payload") if isinstance(messaging.get("payload"), dict) else messaging
    if not isinstance(inbox_obj, dict):
        return result
    capture = contact_capture_from_inbox_payload(inbox_obj)
    inbox_obj["contact_capture"] = capture
    if messaging is not inbox_obj and isinstance(messaging, dict):
        messaging["contact_capture"] = capture
    return result


_REDIS_POLICY_PREFIX = "livechat:contact_policy:"
_POLICY_TTL = 86400 * 7


async def cache_contact_capture_policy(
    *,
    website_token: str | None,
    policy: ContactCapturePolicy | dict[str, Any],
) -> None:
    if not website_token or not str(website_token).strip():
        return
    payload = (
        policy_to_public_dict(policy)
        if isinstance(policy, ContactCapturePolicy)
        else policy
    )
    try:
        await RedisHelper.set_key(
            f"{_REDIS_POLICY_PREFIX}{website_token.strip()}",
            json.dumps(payload, ensure_ascii=False),
            expire_seconds=_POLICY_TTL,
        )
    except Exception:
        logger.exception("Redis cache contact_capture policy thất bại")


async def get_cached_contact_capture_policy(
    website_token: str | None,
) -> dict[str, Any] | None:
    if not website_token or not str(website_token).strip():
        return None
    try:
        raw = await RedisHelper.get_key(
            f"{_REDIS_POLICY_PREFIX}{website_token.strip()}"
        )
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def fetch_contact_capture_for_binding(
    *,
    messaging_account_id: int,
    inbox_id: int,
    website_token: str | None = None,
    db: AsyncSession | None = None,
    tenant_id: UUID | None = None,
) -> dict[str, Any]:
    """
    Resolve policy theo thứ tự:
      1. DB ``messaging_inbox_bindings.contact_capture`` (nguồn sự thật OmniHub)
      2. Redis cache (best-effort)
      3. Derive từ Chatwoot pre_chat
      4. default off
    """
    # 1) DB binding
    if db is not None:
        try:
            from app.services.v1.handle_messaging_inbox_binding import (
                contact_capture_from_binding,
                get_binding_by_tenant_inbox,
                get_binding_by_website_token,
            )

            binding = None
            if tenant_id is not None:
                binding = await get_binding_by_tenant_inbox(
                    db, tenant_id, int(inbox_id)
                )
            if binding is None and website_token:
                binding = await get_binding_by_website_token(db, website_token)
            stored = contact_capture_from_binding(binding)
            if stored is not None:
                public = policy_to_public_dict(parse_contact_capture_policy(stored))
                await cache_contact_capture_policy(
                    website_token=website_token
                    or (binding.website_token if binding else None),
                    policy=public,
                )
                return public
        except Exception:
            logger.exception(
                "Đọc contact_capture từ binding thất bại inbox=%s", inbox_id
            )

    # 2) Redis
    cached = await get_cached_contact_capture_policy(website_token)
    if cached is not None:
        return cached

    # 3) Chatwoot derive
    res = await chatwoot_client.application_request(
        "GET",
        f"/api/v1/accounts/{int(messaging_account_id)}/inboxes/{int(inbox_id)}",
    )
    if res.status_code != 200:
        return policy_to_public_dict(default_contact_capture_policy(enabled=False))
    capture = contact_capture_from_inbox_payload(res.data)
    await cache_contact_capture_policy(website_token=website_token, policy=capture)
    return capture


def extract_contact_fields_from_custom_attributes(
    attrs: Any,
) -> dict[str, str]:
    """Nhận diện contact từ conversation/contact custom_attributes (bot / overlay)."""
    if not isinstance(attrs, dict):
        return {}
    nested = attrs.get("omnihub_contact")
    raw = dict(attrs)
    if isinstance(nested, dict):
        raw = {**raw, **nested}
    cleaned, _ = normalize_contact_payload(
        {
            "name": raw.get("contact_name") or raw.get("name") or raw.get("full_name"),
            "email": raw.get("contact_email") or raw.get("email"),
            "phone": raw.get("contact_phone")
            or raw.get("phone")
            or raw.get("phone_number"),
        }
    )
    return cleaned


async def resolve_contact_id_for_conversation(
    *,
    account_id: int,
    conversation_id: int,
    access_token: str | None = None,
) -> int | None:
    res = await chatwoot_client.application_request(
        "GET",
        f"/api/v1/accounts/{int(account_id)}/conversations/{int(conversation_id)}",
        access_token=access_token,
    )
    if res.status_code != 200:
        return None
    data = res.data
    if isinstance(data, dict) and isinstance(data.get("payload"), dict):
        data = data["payload"]
    return extract_contact_id_from_payload(data if isinstance(data, dict) else None)


async def maybe_apply_pending_contact_from_webhook(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    account_id: int,
    conversation_payload: dict[str, Any],
    event_payload: dict[str, Any] | None = None,
) -> str:
    """
    conversation_created (hoặc tin đầu): Redis pending theo setUser → PATCH contact.
    """
    from app.services.v1.handle_live_chat_public import extract_client_session_candidates

    merged = dict(conversation_payload or {})
    if isinstance(event_payload, dict):
        for k in ("contact", "inbox", "meta"):
            if k not in merged and k in event_payload:
                merged[k] = event_payload[k]

    sessions = extract_client_session_candidates(merged)
    if not sessions and isinstance(event_payload, dict):
        sessions = extract_client_session_candidates(event_payload)
    if not sessions:
        return "no_client_session"

    website_token = extract_website_token_from_payload(merged) or (
        extract_website_token_from_payload(event_payload)
        if isinstance(event_payload, dict)
        else None
    )
    if not website_token:
        inbox_id = None
        for src in (merged, event_payload if isinstance(event_payload, dict) else {}):
            inbox = src.get("inbox") if isinstance(src, dict) else None
            if isinstance(inbox, dict) and inbox.get("id") is not None:
                try:
                    inbox_id = int(inbox["id"])
                except (TypeError, ValueError):
                    pass
            if inbox_id is None and isinstance(src, dict) and src.get("inbox_id") is not None:
                try:
                    inbox_id = int(src["inbox_id"])
                except (TypeError, ValueError):
                    pass
        if inbox_id is not None:
            from app.db.models import MessagingInboxBinding
            from sqlalchemy import select

            q = await db.execute(
                select(MessagingInboxBinding).where(
                    MessagingInboxBinding.tenant_id == tenant_id,
                    MessagingInboxBinding.inbox_id == int(inbox_id),
                )
            )
            binding = q.scalar_one_or_none()
            if binding is not None:
                website_token = binding.website_token

    pending = await consume_pending_contact(
        website_token=website_token,
        client_session_ids=sessions,
        expected_tenant_id=tenant_id,
    )
    if not pending:
        return "no_pending_contact"

    contact_data = pending.get("contact") if isinstance(pending.get("contact"), dict) else {}
    cleaned, errors = normalize_contact_payload(contact_data)
    if errors or not cleaned:
        return f"pending_invalid:{','.join(errors) or 'empty'}"

    contact_id = extract_contact_id_from_payload(merged)
    if contact_id is None and isinstance(event_payload, dict):
        contact_id = extract_contact_id_from_payload(event_payload)
    if contact_id is None:
        return "missing_contact_id"

    ok, status, _ = await upsert_chatwoot_contact(
        account_id=int(account_id),
        contact_id=int(contact_id),
        name=cleaned.get("name"),
        email=cleaned.get("email"),
        phone=cleaned.get("phone"),
        additional_attributes={
            "omnihub_contact_capture": True,
            "omnihub_contact_source": "overlay",
        },
    )
    return f"contact_upserted:{status}" if ok else f"contact_upsert_failed:{status}"


async def maybe_upsert_contact_from_conversation_attrs(
    *,
    account_id: int,
    conversation_payload: dict[str, Any],
    event_payload: dict[str, Any] | None = None,
    access_token: str | None = None,
) -> str:
    """Bot/agent đã ghi custom_attributes → sync sang Contact Chatwoot."""
    attrs: dict[str, Any] = {}
    for src in (conversation_payload, event_payload if isinstance(event_payload, dict) else {}):
        if not isinstance(src, dict):
            continue
        ca = src.get("custom_attributes")
        if isinstance(ca, dict):
            attrs.update(ca)
        contact = src.get("contact")
        if isinstance(contact, dict) and isinstance(contact.get("custom_attributes"), dict):
            attrs.update(contact["custom_attributes"])
        meta = src.get("meta")
        if isinstance(meta, dict):
            sender = meta.get("sender")
            if isinstance(sender, dict) and isinstance(sender.get("custom_attributes"), dict):
                attrs.update(sender["custom_attributes"])

    cleaned = extract_contact_fields_from_custom_attributes(attrs)
    if not cleaned:
        return "no_contact_attrs"

    contact_id = extract_contact_id_from_payload(conversation_payload)
    if contact_id is None and isinstance(event_payload, dict):
        contact_id = extract_contact_id_from_payload(event_payload)
    if contact_id is None:
        return "missing_contact_id"

    ok, status, _ = await upsert_chatwoot_contact(
        account_id=int(account_id),
        contact_id=int(contact_id),
        name=cleaned.get("name"),
        email=cleaned.get("email"),
        phone=cleaned.get("phone"),
        additional_attributes={
            "omnihub_contact_capture": True,
            "omnihub_contact_source": "bot_attrs",
        },
        access_token=access_token,
    )
    return f"contact_attrs_upserted:{status}" if ok else f"contact_attrs_failed:{status}"
