"""
CSAT omnichannel: tạo survey khi resolve (mọi kênh messaging, gồm live chat),
gửi link token, nhận submit public, list theo tenant.
"""

from __future__ import annotations
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.app_config import settings
from app.db.models import (
    ChatwootLegacyMap,
    ChatwootMapResourceType,
    ConversationRating,
    ConversationRatingStatus,
    Tenant,
    User,
)
from app.integrations.chatwoot import client as chatwoot_client
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)
from app.services.v1.handle_chatwoot._shared import (
    _require_tenant_access,
    _resolve_account_id,
)
from app.services.v1.handle_chatwoot.chatbot import send_chatwoot_reply
from app.utils.helpers import is_platform_admin

logger = logging.getLogger(__name__)


def _resend_cooldown() -> timedelta:
    """Khoảng cách tối thiểu giữa 2 lần gửi CSAT cùng conversation."""
    try:
        hours = float(settings.RATING_RESEND_COOLDOWN_HOURS)
    except (TypeError, ValueError):
        hours = 1.0
    if hours < 0:
        hours = 0.0
    return timedelta(hours=hours)


def _anchor_time(row: ConversationRating) -> datetime:
    """Mốc tính cooldown: ưu tiên lúc gửi thành công."""
    ts = row.sent_at or row.created_at
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


async def _tenant_allows_rating(db: AsyncSession, tenant_id: UUID) -> bool:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        return False
    return bool(getattr(tenant, "conversation_rating_enabled", True))


async def _latest_rating_for_conversation(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    messaging_account_id: int,
    conversation_id: int,
) -> ConversationRating | None:
    result = await db.execute(
        select(ConversationRating)
        .where(
            and_(
                ConversationRating.tenant_id == tenant_id,
                ConversationRating.messaging_account_id == messaging_account_id,
                ConversationRating.conversation_id == conversation_id,
            )
        )
        .order_by(ConversationRating.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _in_resend_cooldown(latest: ConversationRating, now: datetime) -> bool:
    cooldown = _resend_cooldown()
    if cooldown.total_seconds() <= 0:
        return False
    return (now - _anchor_time(latest)) < cooldown


def _norm_channel(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _channel_kind(channel: str | None) -> str:
    """Slug ngắn để filter/group (api, web_widget, email, …)."""
    c = (channel or "").strip().lower().replace(" ", "")
    if "webwidget" in c or c in ("website", "web_widget"):
        return "web_widget"
    if "api" in c:
        return "api"
    if "email" in c:
        return "email"
    if "facebook" in c or "instagram" in c or "telegram" in c or "line" in c:
        return "social"
    if "whatsapp" in c:
        return "whatsapp"
    return "other"


def _channel_kind_label(channel: str | None) -> str:
    """Nhãn hiển thị khi không có tên inbox."""
    kind = _channel_kind(channel)
    return {
        "web_widget": "Live chat",
        "api": "API Channel",
        "email": "Email",
        "social": "Social",
        "whatsapp": "WhatsApp",
    }.get(kind, channel or "Khác")


def extract_inbox_from_payload(payload: dict[str, Any] | None) -> tuple[int | None, str | None]:
    """Lấy inbox_id + inbox_name từ webhook / conversation payload."""
    if not isinstance(payload, dict):
        return None, None
    inbox_id: int | None = None
    inbox_name: str | None = None
    for key in ("inbox_id",):
        raw = payload.get(key)
        if raw is not None:
            try:
                inbox_id = int(raw)
            except (TypeError, ValueError):
                pass
    inbox = payload.get("inbox")
    if isinstance(inbox, dict):
        if inbox.get("id") is not None:
            try:
                inbox_id = int(inbox.get("id"))
            except (TypeError, ValueError):
                pass
        name = inbox.get("name")
        if name:
            inbox_name = str(name).strip() or None
    conv = payload.get("conversation")
    if isinstance(conv, dict):
        cid, cname = extract_inbox_from_payload(conv)
        inbox_id = inbox_id or cid
        inbox_name = inbox_name or cname
    return inbox_id, inbox_name


def extract_contact_from_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Lấy contact/sender từ webhook hoặc GET conversation (shape gần Chatwoot CSAT)."""
    if not isinstance(payload, dict):
        return None

    def _normalize(raw: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        cid = raw.get("id")
        if cid is None and not raw.get("name") and not raw.get("email"):
            return None
        return {
            "id": cid,
            "name": raw.get("name"),
            "email": raw.get("email"),
            "phone_number": raw.get("phone_number"),
            "identifier": raw.get("identifier"),
            "thumbnail": raw.get("thumbnail"),
            "availability_status": raw.get("availability_status"),
            "blocked": raw.get("blocked"),
            "custom_attributes": raw.get("custom_attributes") or {},
            "additional_attributes": raw.get("additional_attributes") or {},
            "last_activity_at": raw.get("last_activity_at"),
            "created_at": raw.get("created_at"),
        }

    direct = payload.get("contact")
    if isinstance(direct, dict):
        normalized = _normalize(direct)
        if normalized:
            return normalized

    meta = payload.get("meta")
    if isinstance(meta, dict):
        for key in ("sender", "contact"):
            normalized = _normalize(meta.get(key))
            if normalized:
                return normalized

    conv = payload.get("conversation")
    if isinstance(conv, dict):
        nested = extract_contact_from_payload(conv)
        if nested:
            return nested

    sender = payload.get("sender")
    if isinstance(sender, dict):
        normalized = _normalize(sender)
        if normalized:
            return normalized

    return None


def build_rating_source_meta(
    *,
    channel: str | None,
    inbox_id: int | None,
    inbox_name: str | None,
) -> dict[str, Any]:
    """
    Metadata nguồn để FE truy vết: tên inbox (Zalo OA, Line Chat, …) + loại kênh kỹ thuật.
    """
    channel_type = _norm_channel(channel)
    name = (inbox_name or "").strip() or None
    kind = _channel_kind(channel_type)
    source_label = name or _channel_kind_label(channel_type)
    return {
        "channel_type": channel_type,
        "channel_kind": kind,
        "inbox_id": inbox_id,
        "inbox_name": name,
        "source_label": source_label,
    }


def extract_channel_from_payload(payload: dict[str, Any]) -> str | None:
    """Lấy channel từ webhook / conversation payload Chatwoot."""
    for key in ("channel", "channel_type"):
        if payload.get(key):
            return _norm_channel(payload.get(key))
    meta = payload.get("meta") or {}
    if isinstance(meta, dict) and meta.get("channel"):
        return _norm_channel(meta.get("channel"))
    inbox = payload.get("inbox") or {}
    if isinstance(inbox, dict):
        for key in ("channel_type", "channel"):
            if inbox.get(key):
                return _norm_channel(inbox.get(key))
    conv = payload.get("conversation")
    if isinstance(conv, dict):
        return extract_channel_from_payload(conv)
    return None


def extract_conversation_status(payload: dict[str, Any]) -> str | None:
    status = payload.get("status")
    if status:
        return str(status).lower()
    conv = payload.get("conversation")
    if isinstance(conv, dict) and conv.get("status"):
        return str(conv["status"]).lower()
    return None


def _changed_attributes_list(payload: dict[str, Any]) -> list[Any]:
    raw = payload.get("changed_attributes")
    if raw is None:
        conv = payload.get("conversation")
        if isinstance(conv, dict):
            raw = conv.get("changed_attributes")
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return [raw]
    return []


def _status_change_from_changed_attributes(
    payload: dict[str, Any],
) -> tuple[str | None, str | None]:
    """
    Trả (previous, current) nếu có thay đổi status trong changed_attributes.
    Hỗ trợ vài shape Chatwoot hay gặp.
    """
    for item in _changed_attributes_list(payload):
        if not isinstance(item, dict):
            continue
        # {"status": {"previous_value": "open", "current_value": "resolved"}}
        if "status" in item and isinstance(item["status"], dict):
            st = item["status"]
            prev = st.get("previous_value", st.get("previous"))
            cur = st.get("current_value", st.get("current"))
            if cur is not None:
                return (
                    str(prev).lower() if prev is not None else None,
                    str(cur).lower(),
                )
        # {"status": ["open", "resolved"]} hoặc {"attribute_name":"status",...}
        if "status" in item and isinstance(item["status"], (list, tuple)) and item["status"]:
            vals = list(item["status"])
            prev = vals[0] if len(vals) > 1 else None
            cur = vals[-1]
            return (
                str(prev).lower() if prev is not None else None,
                str(cur).lower(),
            )
        attr = str(item.get("attribute_name") or item.get("name") or "").lower()
        if attr == "status":
            prev = item.get("previous_value", item.get("old_value"))
            cur = item.get("current_value", item.get("new_value", item.get("value")))
            if cur is not None:
                return (
                    str(prev).lower() if prev is not None else None,
                    str(cur).lower(),
                )
    return None, None


def status_became_resolved(payload: dict[str, Any], event_type: str | None) -> bool:
    """
    Case 2: chỉ khi status *chuyển sang* resolved.
    - conversation_status_changed + status hiện tại resolved
    - conversation_updated + changed_attributes cho thấy → resolved (và trước đó khác)
    """
    et = (event_type or payload.get("event") or "").strip()
    current = extract_conversation_status(payload)
    prev, cur = _status_change_from_changed_attributes(payload)

    if et == "conversation_status_changed":
        # Event chuyên biệt: chỉ chạy nếu đang resolved và (nếu có) trước đó khác resolved
        if current != "resolved" and cur != "resolved":
            return False
        if cur == "resolved" and prev == "resolved":
            return False
        if current == "resolved" and prev is not None and prev == "resolved":
            return False
        return (cur == "resolved") or (current == "resolved")

    if et == "conversation_updated":
        if cur == "resolved" and prev != "resolved":
            return True
        # Không có changed_attributes status → không phải lần chuyển resolve
        return False

    # Event khác / thiếu tên: chỉ tin changed_attributes rõ ràng
    return cur == "resolved" and prev != "resolved"


def _resolve_dedupe_window() -> timedelta:
    try:
        secs = int(settings.RATING_RESOLVE_DEDUPE_SECONDS)
    except (TypeError, ValueError):
        secs = 60
    if secs < 0:
        secs = 0
    return timedelta(seconds=secs)


def _in_resolve_dedupe(latest: ConversationRating, now: datetime) -> bool:
    """Case 3: chặn double-fire cùng một lần resolve (webhook + API)."""
    window = _resolve_dedupe_window()
    if window.total_seconds() <= 0:
        return False
    created = latest.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now - created) < window


async def _advisory_lock_conversation(
    db: AsyncSession,
    *,
    messaging_account_id: int,
    conversation_id: int,
) -> None:
    """Serialize create/send CSAT theo (account, conversation) trong transaction hiện tại."""
    k1 = int(messaging_account_id) % 2147483647
    k2 = int(conversation_id) % 2147483647
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": k1, "k2": k2},
    )


async def fetch_conversation_channel_meta(
    messaging_account_id: int,
    conversation_id: int,
) -> dict[str, Any]:
    """GET conversation messaging → channel / inbox / agent (+ tên inbox để truy vết nguồn)."""
    path = f"/api/v1/accounts/{messaging_account_id}/conversations/{conversation_id}"
    res = await chatwoot_client.application_request("GET", path)
    out: dict[str, Any] = {
        "channel": None,
        "inbox_id": None,
        "inbox_name": None,
        "agent_chatwoot_id": None,
        "contact": None,
    }
    if res.status_code != 200 or not isinstance(res.data, dict):
        logger.warning(
            "CSAT: GET conversation %s thất bại status=%s",
            conversation_id,
            res.status_code,
        )
        return out
    data = res.data
    out["channel"] = extract_channel_from_payload(data)
    iid, iname = extract_inbox_from_payload(data)
    if data.get("inbox_id") is not None:
        try:
            iid = int(data.get("inbox_id"))
        except (TypeError, ValueError):
            pass
    out["inbox_id"] = iid
    out["inbox_name"] = iname
    if iid is not None and not iname:
        inbox_path = (
            f"/api/v1/accounts/{messaging_account_id}/inboxes/{int(iid)}"
        )
        inbox_res = await chatwoot_client.application_request("GET", inbox_path)
        if inbox_res.status_code == 200 and isinstance(inbox_res.data, dict):
            payload = inbox_res.data.get("payload")
            if isinstance(payload, dict) and payload.get("name"):
                out["inbox_name"] = str(payload["name"]).strip()
            elif inbox_res.data.get("name"):
                out["inbox_name"] = str(inbox_res.data["name"]).strip()
    assignee = None
    meta = data.get("meta")
    if isinstance(meta, dict):
        assignee = meta.get("assignee")
    if assignee is None:
        assignee = data.get("assignee")
    if isinstance(assignee, dict) and assignee.get("id") is not None:
        out["agent_chatwoot_id"] = assignee.get("id")
    contact = extract_contact_from_payload(data)
    if contact:
        out["contact"] = contact
    return out


async def enrich_rating_source_meta(
    *,
    messaging_account_id: int,
    conversation_id: int,
    channel: str | None,
    meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bổ sung inbox_name / channel từ GET conversation khi webhook thiếu."""
    merged = dict(meta) if isinstance(meta, dict) else {}
    if merged.get("channel") is None and channel:
        merged["channel"] = channel
    need_fetch = (
        not merged.get("inbox_name")
        or merged.get("inbox_id") is None
        or not merged.get("channel")
        or not merged.get("contact")
    )
    if need_fetch:
        fetched = await fetch_conversation_channel_meta(
            messaging_account_id, conversation_id
        )
        for key in ("channel", "inbox_id", "inbox_name", "agent_chatwoot_id", "contact"):
            if merged.get(key) is None and fetched.get(key) is not None:
                merged[key] = fetched[key]
    return merged


async def resolve_channel_or_skip(
    *,
    messaging_account_id: int,
    conversation_id: int,
    channel: str | None,
) -> tuple[str | None, dict[str, Any]]:
    """
    Case 4: bắt buộc biết channel trước khi gửi.
    Returns (channel, meta) — meta có thể chứa inbox/agent từ GET bổ sung.
    channel None = bỏ qua (không xác định được kênh).
    """
    meta: dict[str, Any] = {}
    ch = _norm_channel(channel)
    if not ch:
        meta = await fetch_conversation_channel_meta(
            messaging_account_id, conversation_id
        )
        ch = _norm_channel(meta.get("channel"))
    if not ch:
        logger.info(
            "CSAT: không xác định được channel — bỏ qua conv=%s",
            conversation_id,
        )
        return None, meta
    return ch, meta


def _generate_rating_token() -> str:
    """Token ngắn, URL-safe; đủ entropy cho link có hạn dùng (mặc định ~16 ký tự)."""
    nbytes = max(8, min(int(settings.RATING_TOKEN_BYTES or 12), 48))
    return secrets.token_urlsafe(nbytes)[:64]


async def _allocate_unique_rating_token(db: AsyncSession) -> str:
    """Sinh token unique; retry nếu trùng (hiếm với 12 bytes)."""
    for _ in range(8):
        token = _generate_rating_token()
        exists = await db.execute(
            select(ConversationRating.id)
            .where(ConversationRating.token == token)
            .limit(1)
        )
        if exists.scalar_one_or_none() is None:
            return token
    raise RuntimeError("Không sinh được rating token unique sau nhiều lần thử")


def _rating_public_url(token: str) -> str | None:
    base = (settings.PUBLIC_RATING_BASE_URL or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/{token}"


def _rating_to_dict(row: ConversationRating, *, include_token: bool = False) -> dict[str, Any]:
    meta = row.meta_data if isinstance(row.meta_data, dict) else {}
    data: dict[str, Any] = {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "messaging_account_id": row.messaging_account_id,
        "conversation_id": row.conversation_id,
        "channel": row.channel,
        "channel_type": meta.get("channel_type") or row.channel,
        "channel_kind": meta.get("channel_kind"),
        "source_label": meta.get("source_label"),
        "inbox_id": row.inbox_id or meta.get("inbox_id"),
        "inbox_name": meta.get("inbox_name"),
        "agent_chatwoot_id": row.agent_chatwoot_id,
        "score": row.score,
        "comment": row.comment,
        "status": row.status,
        "rating_url": row.rating_url,
        "sent_at": row.sent_at,
        "submitted_at": row.submitted_at,
        "expires_at": row.expires_at,
        "created_at": row.created_at,
        "meta_data": meta,
    }
    if include_token:
        data["token"] = row.token
    return data


def _rating_to_messaging_item(
    row: ConversationRating,
    *,
    contact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shape gần Chatwoot csat_survey_responses — thêm inbox/source + contact."""
    meta = row.meta_data if isinstance(row.meta_data, dict) else {}
    agent_id = row.agent_chatwoot_id
    resolved_contact = contact or meta.get("contact")
    item: dict[str, Any] = {
        "id": str(row.id),
        "rating": row.score,
        "feedback_message": row.comment or "",
        "conversation_id": row.conversation_id,
        "account_id": row.messaging_account_id,
        "message_id": None,
        "inbox_id": row.inbox_id or meta.get("inbox_id"),
        "inbox_name": meta.get("inbox_name"),
        "source_label": meta.get("source_label"),
        "channel": row.channel,
        "channel_type": meta.get("channel_type") or row.channel,
        "channel_kind": meta.get("channel_kind"),
        "status": row.status,
        "assigned_agent": {"id": agent_id} if agent_id is not None else None,
        "agent_chatwoot_id": agent_id,
        "created_at": row.submitted_at or row.created_at,
        "submitted_at": row.submitted_at,
        "sent_at": row.sent_at,
        "expires_at": row.expires_at,
    }
    if isinstance(resolved_contact, dict):
        item["contact"] = resolved_contact
    return item


async def _batch_fetch_contacts_for_ratings(
    rows: list[ConversationRating],
) -> dict[tuple[int, int], dict[str, Any]]:
    """Fetch contact từ messaging cho rating chưa lưu contact trong meta."""
    keys: list[tuple[int, int]] = []
    for row in rows:
        meta = row.meta_data if isinstance(row.meta_data, dict) else {}
        if meta.get("contact"):
            continue
        if row.messaging_account_id is None or row.conversation_id is None:
            continue
        pair = (int(row.messaging_account_id), int(row.conversation_id))
        if pair not in keys:
            keys.append(pair)

    out: dict[tuple[int, int], dict[str, Any]] = {}
    for account_id, conversation_id in keys:
        fetched = await fetch_conversation_channel_meta(account_id, conversation_id)
        contact = fetched.get("contact")
        if isinstance(contact, dict):
            out[(account_id, conversation_id)] = contact
    return out


def _parse_period_bounds(
    since: str | None, until: str | None
) -> tuple[datetime | None, datetime | None]:
    """since/until: unix epoch (giây) hoặc ISO datetime."""

    def _one(raw: str | None) -> datetime | None:
        if not raw:
            return None
        v = raw.strip()
        if not v:
            return None
        if v.isdigit():
            return datetime.fromtimestamp(int(v), tz=timezone.utc)
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    return _one(since), _one(until)


def _rating_query_filters(
    tenant_id: UUID,
    *,
    since: str | None = None,
    until: str | None = None,
    status: str | None = None,
    channel: str | None = None,
    inbox_id: int | None = None,
    agent_chatwoot_id: int | None = None,
) -> list[Any]:
    filters: list[Any] = [ConversationRating.tenant_id == tenant_id]
    since_dt, until_dt = _parse_period_bounds(since, until)
    if since_dt is not None:
        filters.append(ConversationRating.created_at >= since_dt)
    if until_dt is not None:
        filters.append(ConversationRating.created_at <= until_dt)
    if status:
        filters.append(ConversationRating.status == status)
    if channel:
        filters.append(ConversationRating.channel == channel)
    if inbox_id is not None:
        filters.append(ConversationRating.inbox_id == int(inbox_id))
    if agent_chatwoot_id is not None:
        filters.append(ConversationRating.agent_chatwoot_id == int(agent_chatwoot_id))
    return filters


def _ratings_count_from_rows(
    score_rows: list[tuple[Any, Any]],
) -> dict[str, int]:
    out: dict[str, int] = {}
    for score, cnt in score_rows:
        if score is None:
            continue
        out[str(int(score))] = int(cnt)
    return out


def _aggregate_metrics_by_inbox(
    rows: list[tuple[Any, ...]],
) -> list[dict[str, Any]]:
    buckets: dict[int | None, dict[str, Any]] = {}
    for inbox_id, score, status, sent_at, meta_raw, channel in rows:
        meta = meta_raw if isinstance(meta_raw, dict) else {}
        if inbox_id not in buckets:
            buckets[inbox_id] = {
                "inbox_id": inbox_id,
                "inbox_name": meta.get("inbox_name"),
                "source_label": meta.get("source_label") or meta.get("inbox_name"),
                "channel_kind": meta.get("channel_kind"),
                "channel_type": meta.get("channel_type") or channel,
                "ratings_count": {},
                "total_count": 0,
                "total_sent_messages_count": 0,
                "_scores": [],
            }
        bucket = buckets[inbox_id]
        if sent_at is not None:
            bucket["total_sent_messages_count"] += 1
        if (
            status == ConversationRatingStatus.SUBMITTED.value
            and score is not None
        ):
            key = str(int(score))
            bucket["ratings_count"][key] = bucket["ratings_count"].get(key, 0) + 1
            bucket["total_count"] += 1
            bucket["_scores"].append(int(score))

    result: list[dict[str, Any]] = []
    for bucket in buckets.values():
        scores: list[int] = bucket.pop("_scores")
        bucket["average_score"] = (
            round(sum(scores) / len(scores), 2) if scores else None
        )
        result.append(bucket)
    result.sort(
        key=lambda x: (
            -(x.get("total_count") or 0),
            (x.get("inbox_name") or "") or "",
        )
    )
    return result


async def get_ratings_metrics(
    db: AsyncSession,
    current_user: User,
    tenant_id: UUID,
    *,
    since: str | None = None,
    until: str | None = None,
    channel: str | None = None,
    inbox_id: int | None = None,
    agent_chatwoot_id: int | None = None,
):
    """
  CSAT OmniHub — tổng hợp (tương thích shape Chatwoot metrics) + breakdown theo inbox.
    """
    denied = await _require_tenant_access(current_user, tenant_id, db)
    if denied is not None:
        return denied

    try:
        filters = _rating_query_filters(
            tenant_id,
            since=since,
            until=until,
            channel=channel,
            inbox_id=inbox_id,
            agent_chatwoot_id=agent_chatwoot_id,
        )
        submitted_filters = filters + [
            ConversationRating.status == ConversationRatingStatus.SUBMITTED.value,
            ConversationRating.score.isnot(None),
        ]

        score_q = await db.execute(
            select(ConversationRating.score, func.count())
            .where(and_(*submitted_filters))
            .group_by(ConversationRating.score)
        )
        ratings_count = _ratings_count_from_rows(list(score_q.all()))
        total_count = sum(ratings_count.values())

        sent_q = await db.execute(
            select(func.count())
            .select_from(ConversationRating)
            .where(and_(*filters, ConversationRating.sent_at.isnot(None)))
        )
        total_sent = int(sent_q.scalar() or 0)

        pending_q = await db.execute(
            select(func.count())
            .select_from(ConversationRating)
            .where(
                and_(*filters, ConversationRating.status == ConversationRatingStatus.PENDING.value)
            )
        )
        expired_q = await db.execute(
            select(func.count())
            .select_from(ConversationRating)
            .where(
                and_(*filters, ConversationRating.status == ConversationRatingStatus.EXPIRED.value)
            )
        )

        avg_q = await db.execute(
            select(func.avg(ConversationRating.score)).where(and_(*submitted_filters))
        )
        avg_val = avg_q.scalar()
        average_score = round(float(avg_val), 2) if avg_val is not None else None

        breakdown_q = await db.execute(
            select(
                ConversationRating.inbox_id,
                ConversationRating.score,
                ConversationRating.status,
                ConversationRating.sent_at,
                ConversationRating.meta_data,
                ConversationRating.channel,
            ).where(and_(*filters))
        )
        by_inbox = _aggregate_metrics_by_inbox(list(breakdown_q.all()))

        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Lấy CSAT metrics OmniHub thành công",
            {
                "tenant_id": str(tenant_id),
                "total_count": total_count,
                "ratings_count": ratings_count,
                "total_sent_messages_count": total_sent,
                "average_score": average_score,
                "pending_count": int(pending_q.scalar() or 0),
                "expired_count": int(expired_q.scalar() or 0),
                "by_inbox": by_inbox,
            },
        )
    except Exception as e:
        logger.exception("get_ratings_metrics: %s", e)
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Lỗi khi lấy CSAT metrics",
        )


async def list_rating_responses(
    db: AsyncSession,
    current_user: User,
    tenant_id: UUID,
    *,
    page: int = 1,
    page_size: int = 20,
    since: str | None = None,
    until: str | None = None,
    status: str | None = ConversationRatingStatus.SUBMITTED.value,
    channel: str | None = None,
    inbox_id: int | None = None,
    agent_chatwoot_id: int | None = None,
):
    """
    Danh sách chi tiết CSAT — shape `messaging[]` gần Chatwoot, có inbox/source.
    """
    denied = await _require_tenant_access(current_user, tenant_id, db)
    if denied is not None:
        return denied

    try:
        filters = _rating_query_filters(
            tenant_id,
            since=since,
            until=until,
            status=status,
            channel=channel,
            inbox_id=inbox_id,
            agent_chatwoot_id=agent_chatwoot_id,
        )

        total_q = await db.execute(
            select(func.count()).select_from(ConversationRating).where(and_(*filters))
        )
        total = int(total_q.scalar() or 0)

        offset = (page - 1) * page_size
        rows_q = await db.execute(
            select(ConversationRating)
            .where(and_(*filters))
            .order_by(
                ConversationRating.submitted_at.desc().nullslast(),
                ConversationRating.created_at.desc(),
            )
            .offset(offset)
            .limit(page_size)
        )
        rows = list(rows_q.scalars().all())
        contact_map = await _batch_fetch_contacts_for_ratings(rows)

        messaging = []
        for row in rows:
            pair = (
                (int(row.messaging_account_id), int(row.conversation_id))
                if row.messaging_account_id is not None and row.conversation_id is not None
                else None
            )
            contact = contact_map.get(pair) if pair else None
            messaging.append(_rating_to_messaging_item(row, contact=contact))

        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Lấy danh sách CSAT responses thành công",
            {
                "tenant_id": str(tenant_id),
                "messaging": messaging,
                "page": page,
                "page_size": page_size,
                "total": total,
            },
        )
    except Exception as e:
        logger.exception("list_rating_responses: %s", e)
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Lỗi khi lấy danh sách CSAT responses",
        )


async def _ensure_rating_url(row: ConversationRating) -> str | None:
    """Gắn lại URL nếu trước đó thiếu PUBLIC_RATING_BASE_URL."""
    url = (row.rating_url or "").strip() or _rating_public_url(row.token)
    if url and row.rating_url != url:
        row.rating_url = url
    return url or None


async def _send_rating_link(
    row: ConversationRating,
    *,
    messaging_account_id: int,
    conversation_id: int,
) -> bool:
    rating_url = await _ensure_rating_url(row)
    if not rating_url:
        logger.warning(
            "Chưa cấu hình PUBLIC_RATING_BASE_URL — không gửi link (conv=%s)",
            conversation_id,
        )
        return False
    content = (
        "Cảm ơn bạn đã liên hệ với chúng tôi.\n"
        f"Vui lòng đánh giá trải nghiệm hỗ trợ tại: {rating_url}"
    )
    ok = await send_chatwoot_reply(
        account_id=messaging_account_id,
        conversation_id=conversation_id,
        reply_text=content,
    )
    if ok:
        row.sent_at = datetime.now(timezone.utc)
        row.updated_at = row.sent_at
    else:
        logger.warning(
            "Gửi link CSAT thất bại conv=%s account=%s",
            conversation_id,
            messaging_account_id,
        )
    return ok


async def maybe_create_and_send_rating(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    messaging_account_id: int,
    conversation_id: int,
    channel: str | None = None,
    inbox_id: int | None = None,
    agent_chatwoot_id: int | None = None,
    source_inbox_name: str | None = None,
    source_contact: dict[str, Any] | None = None,
    send_message: bool = True,
    skip_throttle: bool = False,
) -> ConversationRating | None:
    """
    Khi resolve (đã xác nhận transition ở caller webhook):
    - Tenant.conversation_rating_enabled = false → bỏ qua
    - Channel bắt buộc; không xác định được → bỏ qua
    - Advisory lock + dedupe giây → chống race webhook/API
    - Pending chưa gửi → retry
    - Cooldown giờ → không tạo survey mới
    - Ngoài cửa sổ → tạo + gửi
    """
    if not await _tenant_allows_rating(db, tenant_id):
        logger.info(
            "CSAT tắt trên tenant=%s — bỏ qua conv=%s",
            tenant_id,
            conversation_id,
        )
        return None

    resolved_channel, fetched_meta = await resolve_channel_or_skip(
        messaging_account_id=messaging_account_id,
        conversation_id=conversation_id,
        channel=channel,
    )
    if resolved_channel is None:
        return None

    seed_meta: dict[str, Any] = dict(fetched_meta)
    if inbox_id is not None:
        seed_meta["inbox_id"] = inbox_id
    if source_inbox_name and str(source_inbox_name).strip():
        seed_meta["inbox_name"] = str(source_inbox_name).strip()
    if isinstance(source_contact, dict):
        seed_meta["contact"] = source_contact

    fetched_meta = await enrich_rating_source_meta(
        messaging_account_id=messaging_account_id,
        conversation_id=conversation_id,
        channel=resolved_channel,
        meta=seed_meta,
    )
    if inbox_id is None and fetched_meta.get("inbox_id") is not None:
        inbox_id = int(fetched_meta["inbox_id"])
    if agent_chatwoot_id is None and fetched_meta.get("agent_chatwoot_id") is not None:
        agent_chatwoot_id = int(fetched_meta["agent_chatwoot_id"])
    if not fetched_meta.get("channel"):
        fetched_meta["channel"] = resolved_channel

    source_meta = build_rating_source_meta(
        channel=resolved_channel,
        inbox_id=inbox_id,
        inbox_name=fetched_meta.get("inbox_name"),
    )
    if isinstance(fetched_meta.get("contact"), dict):
        source_meta["contact"] = fetched_meta["contact"]

    await _advisory_lock_conversation(
        db,
        messaging_account_id=messaging_account_id,
        conversation_id=conversation_id,
    )

    now = datetime.now(timezone.utc)
    latest = await _latest_rating_for_conversation(
        db,
        tenant_id=tenant_id,
        messaging_account_id=messaging_account_id,
        conversation_id=conversation_id,
    )

    if latest is not None:
        if (
            send_message
            and latest.status == ConversationRatingStatus.PENDING.value
            and latest.sent_at is None
        ):
            await _send_rating_link(
                latest,
                messaging_account_id=messaging_account_id,
                conversation_id=conversation_id,
            )
            await db.commit()
            await db.refresh(latest)
            return latest

        if not skip_throttle and _in_resolve_dedupe(latest, now):
            logger.info(
                "CSAT dedupe %.0fs — bỏ qua conv=%s (tránh double webhook/API)",
                _resolve_dedupe_window().total_seconds(),
                conversation_id,
            )
            return latest

        if not skip_throttle and _in_resend_cooldown(latest, now):
            logger.info(
                "CSAT trong cooldown (%.2fh) — bỏ qua conv=%s tenant=%s last=%s",
                _resend_cooldown().total_seconds() / 3600.0,
                conversation_id,
                tenant_id,
                _anchor_time(latest).isoformat(),
            )
            return latest

    expire_hours = max(1, int(settings.RATING_TOKEN_EXPIRE_HOURS or 72))
    token = await _allocate_unique_rating_token(db)
    rating_url = _rating_public_url(token)

    row = ConversationRating(
        tenant_id=tenant_id,
        messaging_account_id=messaging_account_id,
        conversation_id=conversation_id,
        channel=resolved_channel,
        inbox_id=inbox_id,
        agent_chatwoot_id=agent_chatwoot_id,
        status=ConversationRatingStatus.PENDING.value,
        token=token,
        rating_url=rating_url,
        expires_at=now + timedelta(hours=expire_hours),
        meta_data=source_meta,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()

    if send_message:
        await _send_rating_link(
            row,
            messaging_account_id=messaging_account_id,
            conversation_id=conversation_id,
        )

    await db.commit()
    await db.refresh(row)
    return row


async def handle_resolved_conversation_payload(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    messaging_account_id: int,
    payload: dict[str, Any],
    event_type: str | None = None,
) -> None:
    """Webhook: chỉ khi status chuyển → resolved."""
    et = event_type or payload.get("event")
    if not status_became_resolved(payload, et if isinstance(et, str) else None):
        return

    conv = (
        payload.get("conversation")
        if isinstance(payload.get("conversation"), dict)
        else payload
    )
    conversation_id = conv.get("id") if isinstance(conv, dict) else payload.get("id")
    if conversation_id is None:
        conversation_id = payload.get("conversation_id")
    if conversation_id is None:
        logger.warning("CSAT: thiếu conversation_id trong payload resolved")
        return

    channel = extract_channel_from_payload(payload)
    inbox_id, inbox_name = extract_inbox_from_payload(
        conv if isinstance(conv, dict) else payload
    )
    if inbox_id is None and isinstance(conv, dict):
        inbox_id = conv.get("inbox_id")
    if inbox_id is None:
        inbox_id = payload.get("inbox_id")
    if not inbox_name:
        _, inbox_name = extract_inbox_from_payload(payload)

    webhook_contact = extract_contact_from_payload(
        conv if isinstance(conv, dict) else payload
    )
    if webhook_contact is None:
        webhook_contact = extract_contact_from_payload(payload)

    assignee = None
    if isinstance(conv, dict):
        assignee = conv.get("assignee")
    if assignee is None:
        assignee = payload.get("assignee")
    agent_id = None
    if isinstance(assignee, dict):
        agent_id = assignee.get("id")
    # Prefer nested assignee_id nếu có (tránh UUID sau redact / shape lệch)
    if isinstance(conv, dict) and conv.get("assignee_id") is not None:
        agent_id = conv.get("assignee_id")

    from app.services.v1.handle_chatwoot.chatbot import coerce_assignee_id

    try:
        await maybe_create_and_send_rating(
            db,
            tenant_id=tenant_id,
            messaging_account_id=int(messaging_account_id),
            conversation_id=int(conversation_id),
            channel=channel,
            inbox_id=int(inbox_id) if inbox_id is not None else None,
            agent_chatwoot_id=coerce_assignee_id(agent_id),
            source_inbox_name=inbox_name,
            source_contact=webhook_contact,
        )
    except Exception as e:
        logger.exception("CSAT: lỗi khi tạo/gửi rating: %s", e)


async def fetch_channel_and_send_on_resolve(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    messaging_account_id: int,
    conversation_id: int,
) -> None:
    """Sau toggle_status=resolved: resolve channel rồi gửi (dedupe với webhook)."""
    meta = await fetch_conversation_channel_meta(
        messaging_account_id, conversation_id
    )
    try:
        await maybe_create_and_send_rating(
            db,
            tenant_id=tenant_id,
            messaging_account_id=messaging_account_id,
            conversation_id=conversation_id,
            channel=meta.get("channel"),
            inbox_id=int(meta["inbox_id"]) if meta.get("inbox_id") is not None else None,
            agent_chatwoot_id=(
                int(meta["agent_chatwoot_id"])
                if meta.get("agent_chatwoot_id") is not None
                else None
            ),
        )
    except Exception as e:
        logger.exception("CSAT: lỗi sau toggle_status: %s", e)


async def _current_user_messaging_agent_id(
    db: AsyncSession, current_user: User
) -> int | None:
    """Id agent messaging của user (assignee_id trên Chatwoot)."""
    if current_user.chat_id is not None:
        try:
            return int(current_user.chat_id)
        except (TypeError, ValueError):
            pass
    result = await db.execute(
        select(ChatwootLegacyMap).where(
            and_(
                ChatwootLegacyMap.resource_type == ChatwootMapResourceType.USER,
                ChatwootLegacyMap.local_uuid == current_user.id,
            )
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    try:
        return int(row.chatwoot_id)
    except (TypeError, ValueError):
        return None


async def _require_conversation_assignee(
    db: AsyncSession,
    current_user: User,
    *,
    assignee_chatwoot_id: int | None,
) -> dict[str, Any] | None:
    """
    Chỉ agent đang được gán conversation mới được gửi CSAT thủ công.
    Platform admin được bypass (ops).
    Returns api_response error dict nếu bị từ chối, else None.
    """
    if await is_platform_admin(current_user, db):
        return None

    if assignee_chatwoot_id is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.FORBIDDEN,
            "Conversation chưa được gán nhân viên — chỉ người được gán mới gửi được link đánh giá",
        )

    my_agent_id = await _current_user_messaging_agent_id(db, current_user)
    if my_agent_id is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.FORBIDDEN,
            "Tài khoản chưa đồng bộ agent messaging — không thể gửi link đánh giá",
        )

    if int(my_agent_id) != int(assignee_chatwoot_id):
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.FORBIDDEN,
            "Chỉ nhân viên đang được gán vào cuộc trò chuyện này mới gửi được link đánh giá",
        )
    return None


async def send_rating_manually(
    db: AsyncSession,
    current_user: User,
    *,
    tenant_id: UUID,
    conversation_id: int,
    force_resend: bool = False,
):
    """
    Agent chủ động gửi link CSAT qua messaging.
    Chỉ người đang được assign conversation (hoặc platform admin).
    Không yêu cầu conversation đã resolved.
    """
    denied = await _require_tenant_access(current_user, tenant_id, db)
    if denied is not None:
        return denied

    if not await _tenant_allows_rating(db, tenant_id):
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.FORBIDDEN,
            "CSAT đã tắt trên tenant này",
        )

    account_id, _ = await _resolve_account_id(db, tenant_id)
    if account_id is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.NOT_FOUND,
            "Không tìm thấy messaging account cho tenant",
        )

    meta = await fetch_conversation_channel_meta(int(account_id), conversation_id)
    channel = _norm_channel(meta.get("channel"))
    if channel is None and not meta.get("inbox_id"):
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.NOT_FOUND,
            "Không tìm thấy conversation trên messaging",
        )

    assignee_id = meta.get("agent_chatwoot_id")
    assignee_denied = await _require_conversation_assignee(
        db,
        current_user,
        assignee_chatwoot_id=(
            int(assignee_id) if assignee_id is not None else None
        ),
    )
    if assignee_denied is not None:
        return assignee_denied

    if not (settings.PUBLIC_RATING_BASE_URL or "").strip():
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.SERVICE_UNAVAILABLE,
            "Chưa cấu hình PUBLIC_RATING_BASE_URL — không thể tạo link đánh giá",
        )

    try:
        latest_before = await _latest_rating_for_conversation(
            db,
            tenant_id=tenant_id,
            messaging_account_id=int(account_id),
            conversation_id=conversation_id,
        )
        row = await maybe_create_and_send_rating(
            db,
            tenant_id=tenant_id,
            messaging_account_id=int(account_id),
            conversation_id=conversation_id,
            channel=channel,
            inbox_id=int(meta["inbox_id"]) if meta.get("inbox_id") is not None else None,
            agent_chatwoot_id=(
                int(meta["agent_chatwoot_id"])
                if meta.get("agent_chatwoot_id") is not None
                else None
            ),
            source_inbox_name=meta.get("inbox_name"),
            send_message=True,
            skip_throttle=force_resend,
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception("send_rating_manually db: %s", e)
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Lỗi cơ sở dữ liệu khi gửi link đánh giá",
        )
    except Exception as e:
        await db.rollback()
        logger.exception("send_rating_manually: %s", e)
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Lỗi khi gửi link đánh giá",
        )

    if row is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.BAD_REQUEST,
            "Không thể gửi link đánh giá cho conversation này",
        )

    now = datetime.now(timezone.utc)
    if (
        not force_resend
        and latest_before is not None
        and row.id == latest_before.id
        and latest_before.sent_at is not None
        and (
            _in_resend_cooldown(latest_before, now)
            or _in_resolve_dedupe(latest_before, now)
        )
    ):
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.CONFLICT,
            (
                "Đã gửi link đánh giá gần đây. "
                "Dùng force_resend=true để gửi lại."
            ),
            {
                "rating": _rating_to_dict(latest_before),
                "cooldown_hours": _resend_cooldown().total_seconds() / 3600.0,
                "dedupe_seconds": _resolve_dedupe_window().total_seconds(),
            },
        )

    if row.sent_at is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Tạo link thành công nhưng gửi tin nhắn messaging thất bại",
            {"rating": _rating_to_dict(row)},
        )

    created_new = latest_before is None or row.id != latest_before.id
    message = (
        "Đã gửi link đánh giá mới cho khách hàng"
        if created_new
        else "Đã gửi lại link đánh giá cho khách hàng"
    )
    return api_response(
        ResponseStatus.SUCCESS,
        ResponseStatusCode.OK,
        message,
        {
            "rating": _rating_to_dict(row),
            "sent": True,
            "created_new": created_new,
        },
    )


async def get_rating_by_token(token: str, db: AsyncSession):
    """Public: thông tin form đánh giá (không lộ nội bộ thừa)."""
    try:
        result = await db.execute(
            select(ConversationRating).where(ConversationRating.token == token)
        )
        row = result.scalar_one_or_none()
        if not row:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Link đánh giá không tồn tại",
            )

        now = datetime.now(timezone.utc)
        if (
            row.status == ConversationRatingStatus.PENDING.value
            and row.expires_at
            and row.expires_at < now
        ):
            row.status = ConversationRatingStatus.EXPIRED.value
            await db.commit()

        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Thông tin đánh giá",
            {
                "status": row.status,
                "channel": row.channel,
                "score": row.score,
                "expires_at": row.expires_at,
                "can_submit": row.status == ConversationRatingStatus.PENDING.value,
            },
        )
    except Exception as e:
        logger.exception("get_rating_by_token: %s", e)
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Lỗi khi lấy thông tin đánh giá",
        )


async def submit_rating(token: str, score: int, comment: str | None, db: AsyncSession):
    """Public: nộp điểm 1–5."""
    try:
        result = await db.execute(
            select(ConversationRating)
            .where(ConversationRating.token == token)
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if not row:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Link đánh giá không tồn tại",
            )

        now = datetime.now(timezone.utc)
        if row.status == ConversationRatingStatus.SUBMITTED.value:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.CONFLICT,
                "Bạn đã gửi đánh giá trước đó",
            )
        if row.status == ConversationRatingStatus.EXPIRED.value or (
            row.expires_at and row.expires_at < now
        ):
            if row.status != ConversationRatingStatus.EXPIRED.value:
                row.status = ConversationRatingStatus.EXPIRED.value
                await db.commit()
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Link đánh giá đã hết hạn",
            )
        if row.status != ConversationRatingStatus.PENDING.value:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Không thể gửi đánh giá ở trạng thái hiện tại",
            )

        row.score = score
        row.comment = (comment or "").strip() or None
        row.status = ConversationRatingStatus.SUBMITTED.value
        row.submitted_at = now
        row.updated_at = now
        await db.commit()

        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Cảm ơn bạn đã đánh giá",
            {"score": row.score, "status": row.status},
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception("submit_rating db: %s", e)
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Lỗi cơ sở dữ liệu",
        )
    except Exception as e:
        await db.rollback()
        logger.exception("submit_rating: %s", e)
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Lỗi khi gửi đánh giá",
        )


async def list_ratings(
    db: AsyncSession,
    current_user: User,
    tenant_id: UUID,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    channel: str | None = None,
    inbox_id: int | None = None,
):
    denied = await _require_tenant_access(current_user, tenant_id, db)
    if denied is not None:
        return denied

    try:
        filters = [ConversationRating.tenant_id == tenant_id]
        if status:
            filters.append(ConversationRating.status == status)
        if channel:
            filters.append(ConversationRating.channel == channel)
        if inbox_id is not None:
            filters.append(ConversationRating.inbox_id == int(inbox_id))

        from sqlalchemy import func

        total_q = await db.execute(
            select(func.count()).select_from(ConversationRating).where(and_(*filters))
        )
        total = int(total_q.scalar() or 0)

        offset = (page - 1) * page_size
        rows_q = await db.execute(
            select(ConversationRating)
            .where(and_(*filters))
            .order_by(ConversationRating.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        rows = list(rows_q.scalars().all())

        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Danh sách đánh giá hội thoại",
            {
                "items": [_rating_to_dict(r) for r in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
            },
        )
    except Exception as e:
        logger.exception("list_ratings: %s", e)
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Lỗi khi lấy danh sách đánh giá",
        )
