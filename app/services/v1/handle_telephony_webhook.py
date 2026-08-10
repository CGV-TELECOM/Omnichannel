"""
Telephony webhook: nhận event từ tổng đài, map theo sip_call_id,
upsert call_logs + append call_log_events. Multi-tenant via call_logs
hoặc webcall_config (domain_uuid / hotline / sip_domain).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import CallLog, CallLogEvent, CallLogStatus, Tenant, User, Customer
from app.schemas.requests.call_log import CallLogResponse
from app.schemas.responses.api_response_rule import (
    api_response,
    ResponseStatus,
    ResponseStatusCode,
)

logger = logging.getLogger(__name__)
PBX_LOCAL_TZ = timezone(timedelta(hours=7))

# Thứ tự trạng thái (số càng lớn càng “sau”). Không cho lùi trừ CDR metrics.
_STATUS_RANK = {
    CallLogStatus.CREATED.value: 0,
    CallLogStatus.RINGING.value: 10,
    CallLogStatus.ANSWERED.value: 20,
    CallLogStatus.BUSY.value: 30,
    CallLogStatus.NO_ANSWER.value: 30,
    CallLogStatus.FAILED.value: 30,
    CallLogStatus.MISSED.value: 30,
    CallLogStatus.ENDED.value: 40,
}

# Key đã map sang cột typed / không nhét vào meta_data (tránh trùng).
# Raw đầy đủ nằm ở call_log_events.payload.
_COLUMN_MAPPED_KEYS = frozenset(
    {
        "sip_call_id",
        "call_id",
        "state",
        "direction",
        "from_number",
        "to_number",
        "hotline",
        "time_started",
        "time_answered",
        "time_ended",
        "duration",
        "billsec",
        "recording_url",
        "recording",
        "record_url",
        "record_file",
        "recording_file",
        "file_recording",
        "record_path",
    }
)

_RECORDING_ALIASES = (
    "recording_url",
    "recording",
    "record_url",
    "record_file",
    "recording_file",
    "file_recording",
    "record_path",
)


def _pick_recording_url(payload: dict) -> Optional[str]:
    for key in _RECORDING_ALIASES:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extract_meta(payload: dict, existing: Optional[dict] = None) -> dict:
    """
    meta_data chỉ giữ field chưa có cột typed.
    Raw từng event: call_log_events.payload (không nhân bản last_payload).
    """
    meta = {
        k: v
        for k, v in (existing or {}).items()
        if k not in _COLUMN_MAPPED_KEYS and k != "last_payload" and v not in (None, "")
    }
    for key, value in payload.items():
        if key in _COLUMN_MAPPED_KEYS or key == "last_payload":
            continue
        if value in (None, ""):
            continue
        meta[key] = value

    # domain / domain_name thường trùng
    if meta.get("domain") and meta.get("domain_name") == meta.get("domain"):
        meta.pop("domain_name", None)
    return meta


def _apply_payload_fields(call_log: CallLog, payload: dict, *, now: datetime, mapped_status: str) -> None:
    """Map field webhook → cột call_logs; phần còn lại → meta_data gọn."""
    provider_call_id = _parse_uuid(payload.get("call_id"))
    from_number = str(payload.get("from_number") or "").strip() or None
    to_number = str(payload.get("to_number") or "").strip() or None
    hotline = str(payload.get("hotline") or "").strip() or None
    direction = str(payload.get("direction") or call_log.direction or "outbound").lower().strip()

    if provider_call_id:
        call_log.provider_call_id = provider_call_id
    if from_number:
        call_log.from_number = from_number
    if to_number:
        call_log.to_number = to_number
    if hotline:
        call_log.hotline = hotline
    if direction:
        call_log.direction = direction

    if direction == "inbound":
        phone = _normalize_phone(from_number) or _normalize_phone(to_number)
    else:
        phone = _normalize_phone(to_number) or _normalize_phone(from_number)
    if phone:
        call_log.phone_number = phone[:20]

    if _can_advance(call_log.status, mapped_status):
        call_log.status = mapped_status

    ts = _parse_dt(payload.get("time_started"))
    ta = _parse_dt(payload.get("time_answered"))
    te = _parse_dt(payload.get("time_ended"))
    state_raw = str(payload.get("state") or "").lower().strip()

    if not ta and (
        mapped_status == CallLogStatus.ANSWERED.value
        or str(payload.get("status") or "").lower() == "answered"
        or state_raw == "answered"
    ):
        ta = now
    if not te and state_raw in ("hangup", "cdr", "ended"):
        te = now
    if not te and mapped_status in (
        CallLogStatus.ENDED.value,
        CallLogStatus.MISSED.value,
        CallLogStatus.BUSY.value,
        CallLogStatus.NO_ANSWER.value,
        CallLogStatus.FAILED.value,
    ):
        te = now

    if ts:
        if not call_log.started_at or state_raw in ("cdr", "hangup") or ts < call_log.started_at:
            call_log.started_at = ts
    if ta:
        if not call_log.answered_at or state_raw in ("cdr", "hangup"):
            call_log.answered_at = ta
    if te:
        call_log.ended_at = te

    duration = _as_int(payload.get("duration"))
    billsec = _as_int(payload.get("billsec"))
    if duration is not None:
        call_log.duration = duration
    if billsec is not None:
        call_log.billsec = billsec

    recording = _pick_recording_url(payload)
    if recording:
        call_log.recording_url = recording[:512]

    call_log.meta_data = _extract_meta(payload, call_log.meta_data)
    call_log.updated_at = now


def _serialize_call_log(call_log: CallLog) -> dict:
    return CallLogResponse.model_validate(call_log).model_dump(mode="json")


def _parse_uuid(value: Any) -> Optional[UUID]:
    if value is None or value == "":
        return None
    try:
        return UUID(str(value).strip())
    except (ValueError, AttributeError, TypeError):
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=PBX_LOCAL_TZ)
    s = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S.%f",
    ):
        try:
            dt = datetime.strptime(s.replace("Z", "+0000"), fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=PBX_LOCAL_TZ)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=PBX_LOCAL_TZ)
    except ValueError:
        return None


def _normalize_phone(raw: Optional[str]) -> str:
    if not raw:
        return ""
    return "".join(ch for ch in str(raw) if ch.isdigit() or ch == "+")


def _phone_digits(raw: Optional[str]) -> str:
    """Chỉ giữ chữ số để so khớp ổn định."""
    if not raw:
        return ""
    return "".join(ch for ch in str(raw) if ch.isdigit())


def _phone_match_variants(phone: str) -> set[str]:
    """
    Sinh biến thể VN thường gặp để exact-match:
    0901... ↔ 84901... ↔ +84901...
    """
    digits = _phone_digits(phone)
    if not digits:
        return set()

    variants = {digits}
    if digits.startswith("84") and len(digits) >= 11:
        variants.add("0" + digits[2:])
        variants.add(f"+{digits}")
    elif digits.startswith("0") and len(digits) >= 10:
        intl = "84" + digits[1:]
        variants.add(intl)
        variants.add(f"+{intl}")
    else:
        variants.add(f"+{digits}")
    return variants


def _map_status(state: str, direction: str, payload: dict) -> str:
    s = (state or "").lower().strip()
    direction = (direction or "outbound").lower()

    if s in ("ringing", "created"):
        return CallLogStatus.RINGING.value if s == "ringing" else CallLogStatus.CREATED.value
    if s == "answered":
        return CallLogStatus.ANSWERED.value
    if s in ("hangup", "ended", "cdr"):
        # inbound không trả lời → missed
        answered = payload.get("time_answered") or payload.get("status") == "answered"
        billsec = int(payload.get("billsec") or 0)
        if direction == "inbound" and not answered and billsec <= 0:
            return CallLogStatus.MISSED.value
        if direction == "outbound" and not answered and billsec <= 0:
            cdr_status = (payload.get("status") or "").lower()
            if cdr_status in ("busy",):
                return CallLogStatus.BUSY.value
            if cdr_status in ("no_answer", "noanswer", "failed"):
                return CallLogStatus.NO_ANSWER.value if "answer" in cdr_status or cdr_status == "noanswer" else CallLogStatus.FAILED.value
            if s == "cdr" and cdr_status and cdr_status != "answered":
                return CallLogStatus.NO_ANSWER.value
            if s == "hangup" and not answered:
                return CallLogStatus.NO_ANSWER.value
        return CallLogStatus.ENDED.value
    if s in ("busy",):
        return CallLogStatus.BUSY.value
    if s in ("missed",):
        return CallLogStatus.MISSED.value
    if s in ("no_answer", "noanswer"):
        return CallLogStatus.NO_ANSWER.value
    if s in ("failed", "error"):
        return CallLogStatus.FAILED.value
    return s or CallLogStatus.RINGING.value


def _can_advance(current: Optional[str], new: str) -> bool:
    if not current:
        return True
    return _STATUS_RANK.get(new, 0) >= _STATUS_RANK.get(current, 0)


def _idempotency_key(sip_call_id: UUID, state: str, payload: dict) -> str:
    parts = [
        str(sip_call_id),
        state,
        str(payload.get("time_started") or ""),
        str(payload.get("time_answered") or ""),
        str(payload.get("time_ended") or ""),
        str(payload.get("recording_url") or ""),
        str(payload.get("application") or ""),
        str(payload.get("billsec") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:128]


async def _resolve_tenant_id(
    db: AsyncSession,
    *,
    existing: Optional[CallLog],
    payload: dict,
) -> Optional[UUID]:
    if existing:
        return existing.tenant_id

    domain_uuid = str(payload.get("domain_uuid") or "").strip()
    domain = str(payload.get("domain") or payload.get("domain_name") or "").strip().lower()
    hotline = _normalize_phone(payload.get("hotline") or payload.get("to_number"))

    result = await db.execute(select(Tenant).where(Tenant.is_active == 1))
    tenants = result.scalars().all()

    if domain_uuid:
        for t in tenants:
            cfg = t.webcall_config or {}
            if str(cfg.get("domain_uuid") or "").strip() == domain_uuid:
                return t.id

    if domain:
        for t in tenants:
            cfg = t.webcall_config or {}
            sip_domain = str(cfg.get("sip_domain") or "").strip().lower()
            if sip_domain and sip_domain == domain:
                return t.id

    if hotline:
        for t in tenants:
            cfg = t.webcall_config or {}
            hotlines = cfg.get("hotlines") or []
            if isinstance(hotlines, str):
                hotlines = [hotlines]
            normalized = {_normalize_phone(h) for h in hotlines}
            if hotline in normalized:
                return t.id

    return None


async def _find_user_by_extension(db: AsyncSession, tenant_id: UUID, extension: Optional[str]) -> Optional[UUID]:
    ext = (extension or "").strip()
    if not ext:
        return None
    result = await db.execute(
        select(User.id).where(
            User.tenant_id == tenant_id,
            User.sip_extension == ext,
            User.is_active == 1,
            # None / True = bật; chỉ bỏ qua khi tắt tường minh
            User.call_log_enabled.is_not(False),
        )
    )
    return result.scalar_one_or_none()


async def _find_customer_by_phone(db: AsyncSession, tenant_id: UUID, phone: str) -> Optional[UUID]:
    """
    Match khách theo SĐT an toàn:
    1) Exact match trên các biến thể chuẩn hóa (0x / 84x / +84x)
    2) Fallback đuôi 9 số — CHỈ khi đúng 1 khách trong tenant
    Ambiguous → None (không đoán).

    Chi tiết: docs/call_customer_phone_match.md
    """
    digits = _phone_digits(phone)
    if not digits:
        return None

    variants = _phone_match_variants(digits)
    if variants:
        exact = await db.execute(
            select(Customer.id).where(
                Customer.tenant_id == tenant_id,
                Customer.is_active == 1,
                Customer.phone.in_(list(variants)),
            )
        )
        exact_ids = list(exact.scalars().all())
        if len(exact_ids) == 1:
            return exact_ids[0]
        if len(exact_ids) > 1:
            logger.warning(
                "[telephony] ambiguous exact phone match tenant=%s phone=%s count=%s",
                tenant_id,
                digits,
                len(exact_ids),
            )
            return None

    # Suffix fallback: chỉ khi đủ dài và unique
    suffix = digits[-9:] if len(digits) >= 9 else ""
    if len(suffix) < 9:
        return None

    phone_digits_expr = func.regexp_replace(Customer.phone, "[^0-9]", "", "g")
    suffix_q = await db.execute(
        select(Customer.id).where(
            Customer.tenant_id == tenant_id,
            Customer.is_active == 1,
            Customer.phone.isnot(None),
            func.right(phone_digits_expr, 9) == suffix,
        )
    )
    suffix_ids = list(suffix_q.scalars().all())
    if len(suffix_ids) == 1:
        return suffix_ids[0]
    if len(suffix_ids) > 1:
        logger.warning(
            "[telephony] ambiguous suffix phone match tenant=%s suffix=%s count=%s",
            tenant_id,
            suffix,
            len(suffix_ids),
        )
    return None


async def handle_telephony_webhook(db: AsyncSession, payload: dict):
    """
    Public webhook handler — không JWT.
    Bắt buộc có sip_call_id (UUID).
    """
    try:
        sip_call_id = _parse_uuid(payload.get("sip_call_id"))
        if not sip_call_id:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message="Thiếu hoặc sai sip_call_id (bắt buộc UUID)",
                data=None,
            )

        provider_call_id = _parse_uuid(payload.get("call_id"))
        state_raw = str(payload.get("state") or "unknown").lower().strip()
        direction = str(payload.get("direction") or "outbound").lower().strip()
        application = payload.get("application")
        from_number = str(payload.get("from_number") or "").strip() or None
        to_number = str(payload.get("to_number") or "").strip() or None
        hotline = str(payload.get("hotline") or "").strip() or None

        # Số KH: inbound = from, outbound = to
        if direction == "inbound":
            phone_number = _normalize_phone(from_number) or _normalize_phone(to_number) or "unknown"
        else:
            phone_number = _normalize_phone(to_number) or _normalize_phone(from_number) or "unknown"

        mapped_status = _map_status(state_raw, direction, payload)
        now = datetime.now(timezone.utc)
        event_at = (
            _parse_dt(payload.get("time_ended"))
            or _parse_dt(payload.get("time_answered"))
            or _parse_dt(payload.get("time_started"))
            or now
        )

        # 1) Tìm call_log theo sip_call_id
        result = await db.execute(select(CallLog).where(CallLog.sip_call_id == sip_call_id))
        call_log = result.scalar_one_or_none()

        tenant_id = await _resolve_tenant_id(db, existing=call_log, payload=payload)
        if not tenant_id:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message=(
                    "Không xác định được tenant. "
                    "Cấu hình webcall_config.domain_uuid/hotlines/sip_domain "
                    "hoặc tạo call_logs trước (outbound web)."
                ),
                data={"sip_call_id": str(sip_call_id)},
            )

        # 2) Tạo mới nếu chưa có (inbound / webhook-first)
        created = False
        if not call_log:
            user_id = None
            customer_id = None
            if direction == "outbound" and from_number:
                user_id = await _find_user_by_extension(db, tenant_id, from_number)
            if direction == "inbound" and from_number:
                customer_id = await _find_customer_by_phone(db, tenant_id, phone_number)
                user_id = await _find_user_by_extension(db, tenant_id, to_number)

            call_log = CallLog(
                tenant_id=tenant_id,
                sip_call_id=sip_call_id,
                direction=direction,
                phone_number=phone_number[:20],
                status=mapped_status,
                source="webhook",
                started_at=now,
                created_at=now,
                updated_at=now,
                meta_data={},
            )
            db.add(call_log)
            await db.flush()
            created = True
            call_log.user_id = user_id
            call_log.customer_id = customer_id

        # 3) Map đủ field từ payload → cột + meta_data (kể cả recording_url / CDR)
        _apply_payload_fields(call_log, payload, now=now, mapped_status=mapped_status)

        if not call_log.user_id:
            if direction == "inbound":
                call_log.user_id = await _find_user_by_extension(db, tenant_id, to_number)
            elif direction == "outbound":
                call_log.user_id = await _find_user_by_extension(db, tenant_id, from_number)
        if not call_log.customer_id and direction == "inbound":
            call_log.customer_id = await _find_customer_by_phone(db, tenant_id, phone_number)

        # 4) Append event (idempotent) — raw payload luôn đủ
        idem = _idempotency_key(sip_call_id, state_raw, payload)
        existing_event = await db.execute(
            select(CallLogEvent.id).where(CallLogEvent.idempotency_key == idem)
        )
        event_created = False
        if existing_event.scalar_one_or_none() is None:
            event = CallLogEvent(
                call_log_id=call_log.id,
                tenant_id=tenant_id,
                sip_call_id=sip_call_id,
                provider_call_id=provider_call_id,
                state=state_raw,
                application=str(application) if application else None,
                event_at=event_at,
                received_at=now,
                payload=payload,
                idempotency_key=idem,
            )
            db.add(event)
            event_created = True

        await db.commit()
        await db.refresh(call_log)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK if not created else ResponseStatusCode.CREATED,
            message="Đã xử lý telephony webhook",
            data={
                "call_log": _serialize_call_log(call_log),
                "event": {
                    "state": state_raw,
                    "application": str(application) if application else None,
                    "event_at": event_at.isoformat() if event_at else None,
                    "call_log_created": created,
                    "event_created": event_created,
                },
            },
        )

    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception("[TELEPHONY WEBHOOK] DB error: %s", e)
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi database khi xử lý webhook",
            data=None,
        )
    except Exception as e:
        await db.rollback()
        logger.exception("[TELEPHONY WEBHOOK] error: %s", e)
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message=f"Lỗi xử lý webhook: {e}",
            data=None,
        )
