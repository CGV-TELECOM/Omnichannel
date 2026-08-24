"""
CSAT omnichannel MVP: tạo survey khi resolve (kênh ngoài web widget),
gửi link token, nhận submit public, list theo tenant.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.app_config import settings
from app.db.models import ConversationRating, ConversationRatingStatus, Tenant, User
from app.integrations.chatwoot import client as chatwoot_client
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)
from app.services.v1.handle_chatwoot._shared import _require_tenant_access
from app.services.v1.handle_chatwoot.chatbot import send_chatwoot_reply

logger = logging.getLogger(__name__)

# Live chat dùng CSAT native Chatwoot — không gửi link OmniHub.
_SKIP_CHANNELS = frozenset(
    {
        "channel::webwidget",
        "channel::website",
        "web_widget",
        "website",
        "Channel::WebWidget",
        "Channel::Website",
    }
)


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


def _is_web_widget_channel(channel: str | None) -> bool:
    if not channel:
        return False
    c = channel.strip()
    if c in _SKIP_CHANNELS:
        return True
    return c.lower().replace(" ", "") in {
        x.lower().replace(" ", "") for x in _SKIP_CHANNELS
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
    """GET conversation messaging → channel / inbox / agent."""
    path = f"/api/v1/accounts/{messaging_account_id}/conversations/{conversation_id}"
    res = await chatwoot_client.application_request("GET", path)
    out: dict[str, Any] = {
        "channel": None,
        "inbox_id": None,
        "agent_chatwoot_id": None,
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
    out["inbox_id"] = data.get("inbox_id")
    assignee = None
    meta = data.get("meta")
    if isinstance(meta, dict):
        assignee = meta.get("assignee")
    if assignee is None:
        assignee = data.get("assignee")
    if isinstance(assignee, dict) and assignee.get("id") is not None:
        out["agent_chatwoot_id"] = assignee.get("id")
    return out


async def resolve_channel_or_skip(
    *,
    messaging_account_id: int,
    conversation_id: int,
    channel: str | None,
) -> tuple[str | None, dict[str, Any]]:
    """
    Case 4: bắt buộc biết channel trước khi gửi.
    Returns (channel, meta) — meta có thể chứa inbox/agent từ GET bổ sung.
    channel None = bỏ qua (unknown hoặc web widget).
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
            "CSAT: không xác định được channel — bỏ qua conv=%s (tránh gửi nhầm web widget)",
            conversation_id,
        )
        return None, meta
    if _is_web_widget_channel(ch):
        logger.info(
            "Bỏ qua CSAT OmniHub cho web widget conversation=%s channel=%s",
            conversation_id,
            ch,
        )
        return None, meta
    return ch, meta


def _rating_public_url(token: str) -> str | None:
    base = (settings.PUBLIC_RATING_BASE_URL or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/{token}"


def _rating_to_dict(row: ConversationRating, *, include_token: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "messaging_account_id": row.messaging_account_id,
        "conversation_id": row.conversation_id,
        "channel": row.channel,
        "inbox_id": row.inbox_id,
        "agent_chatwoot_id": row.agent_chatwoot_id,
        "score": row.score,
        "comment": row.comment,
        "status": row.status,
        "rating_url": row.rating_url,
        "sent_at": row.sent_at,
        "submitted_at": row.submitted_at,
        "expires_at": row.expires_at,
        "created_at": row.created_at,
    }
    if include_token:
        data["token"] = row.token
    return data


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
    send_message: bool = True,
) -> ConversationRating | None:
    """
    Khi resolve (đã xác nhận transition ở caller webhook):
    - Tenant.conversation_rating_enabled = false → bỏ qua
    - Channel bắt buộc; web widget / unknown → bỏ qua
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

    if inbox_id is None and fetched_meta.get("inbox_id") is not None:
        inbox_id = int(fetched_meta["inbox_id"])
    if agent_chatwoot_id is None and fetched_meta.get("agent_chatwoot_id") is not None:
        agent_chatwoot_id = int(fetched_meta["agent_chatwoot_id"])
    if inbox_id is None or agent_chatwoot_id is None:
        if not fetched_meta:
            fetched_meta = await fetch_conversation_channel_meta(
                messaging_account_id, conversation_id
            )
            if inbox_id is None and fetched_meta.get("inbox_id") is not None:
                inbox_id = int(fetched_meta["inbox_id"])
            if (
                agent_chatwoot_id is None
                and fetched_meta.get("agent_chatwoot_id") is not None
            ):
                agent_chatwoot_id = int(fetched_meta["agent_chatwoot_id"])

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

        if _in_resolve_dedupe(latest, now):
            logger.info(
                "CSAT dedupe %.0fs — bỏ qua conv=%s (tránh double webhook/API)",
                _resolve_dedupe_window().total_seconds(),
                conversation_id,
            )
            return latest

        if _in_resend_cooldown(latest, now):
            logger.info(
                "CSAT trong cooldown (%.2fh) — bỏ qua conv=%s tenant=%s last=%s",
                _resend_cooldown().total_seconds() / 3600.0,
                conversation_id,
                tenant_id,
                _anchor_time(latest).isoformat(),
            )
            return latest

    expire_hours = max(1, int(settings.RATING_TOKEN_EXPIRE_HOURS or 72))
    token = secrets.token_urlsafe(32)[:64]
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
    inbox_id = None
    if isinstance(conv, dict):
        inbox_id = conv.get("inbox_id")
    if inbox_id is None:
        inbox_id = payload.get("inbox_id")

    assignee = None
    if isinstance(conv, dict):
        assignee = conv.get("assignee")
    if assignee is None:
        assignee = payload.get("assignee")
    agent_id = None
    if isinstance(assignee, dict):
        agent_id = assignee.get("id")

    try:
        await maybe_create_and_send_rating(
            db,
            tenant_id=tenant_id,
            messaging_account_id=int(messaging_account_id),
            conversation_id=int(conversation_id),
            channel=channel,
            inbox_id=int(inbox_id) if inbox_id is not None else None,
            agent_chatwoot_id=int(agent_id) if agent_id is not None else None,
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
