"""Map website_token (Chatwoot live chat) → OmniHub tenant/inbox."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MessagingInboxBinding, generate_uuid7
from app.integrations.chatwoot import client as chatwoot_client

logger = logging.getLogger(__name__)


def _extract_inbox_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("payload", "data", "inboxes"):
        raw = data.get(key)
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
    # single inbox object
    if data.get("id") is not None:
        return [data]
    return []


def _inbox_website_token(inbox: dict[str, Any]) -> str | None:
    for key in ("website_token", "web_widget_script", "token"):
        raw = inbox.get(key)
        if raw and str(raw).strip():
            return str(raw).strip()
    # nested channel config
    for nest_key in ("channel", "web_widget", "website_channel"):
        nest = inbox.get(nest_key)
        if isinstance(nest, dict):
            tok = nest.get("website_token") or nest.get("token")
            if tok and str(tok).strip():
                return str(tok).strip()
    return None


def _inbox_channel_type(inbox: dict[str, Any]) -> str | None:
    for key in ("channel_type", "channel"):
        raw = inbox.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, dict):
            t = raw.get("type") or raw.get("channel_type")
            if t:
                return str(t).strip()
    return None


async def upsert_inbox_bindings_from_payload(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    messaging_account_id: int,
    inboxes_payload: Any,
) -> int:
    """Đồng bộ binding từ danh sách inbox Chatwoot. Returns số row upsert."""
    items = _extract_inbox_items(inboxes_payload)
    now = datetime.now(timezone.utc)
    upserted = 0
    seen_inbox_ids: set[int] = set()

    for inbox in items:
        try:
            inbox_id = int(inbox.get("id"))
        except (TypeError, ValueError):
            continue
        token = _inbox_website_token(inbox)
        if not token:
            continue
        seen_inbox_ids.add(inbox_id)
        channel_type = _inbox_channel_type(inbox)
        name = inbox.get("name")
        name_s = str(name).strip() if name else None

        q = await db.execute(
            select(MessagingInboxBinding).where(
                MessagingInboxBinding.tenant_id == tenant_id,
                MessagingInboxBinding.inbox_id == inbox_id,
            )
        )
        row = q.scalar_one_or_none()
        if row is None:
            # website_token unique — nếu token đã thuộc inbox khác, cập nhật inbox đó
            q2 = await db.execute(
                select(MessagingInboxBinding).where(
                    MessagingInboxBinding.website_token == token
                )
            )
            by_token = q2.scalar_one_or_none()
            if by_token is not None:
                row = by_token
            else:
                row = MessagingInboxBinding(
                    id=generate_uuid7(),
                    tenant_id=tenant_id,
                    created_at=now,
                )
                db.add(row)

        row.tenant_id = tenant_id
        row.messaging_account_id = int(messaging_account_id)
        row.inbox_id = inbox_id
        row.website_token = token
        row.inbox_name = name_s
        row.channel_type = channel_type
        row.is_active = True
        row.updated_at = now
        upserted += 1

    # Soft-deactivate bindings của tenant không còn trong list inbox
    if items:
        q_all = await db.execute(
            select(MessagingInboxBinding).where(
                MessagingInboxBinding.tenant_id == tenant_id
            )
        )
        for row in q_all.scalars().all():
            if row.inbox_id not in seen_inbox_ids:
                row.is_active = False
                row.updated_at = now

    await db.flush()
    return upserted


async def sync_tenant_inbox_bindings(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    messaging_account_id: int,
) -> int:
    """GET inboxes từ Chatwoot rồi upsert bindings."""
    res = await chatwoot_client.application_request(
        "GET",
        f"/api/v1/accounts/{messaging_account_id}/inboxes",
    )
    if res.status_code != 200:
        logger.warning(
            "Sync inbox bindings thất bại tenant=%s status=%s",
            tenant_id,
            res.status_code,
        )
        return 0
    n = await upsert_inbox_bindings_from_payload(
        db,
        tenant_id=tenant_id,
        messaging_account_id=messaging_account_id,
        inboxes_payload=res.data,
    )
    hmac_off = await ensure_web_widget_hmac_optional_for_anonymous(
        messaging_account_id=int(messaging_account_id),
        inboxes_payload=res.data,
    )
    if hmac_off:
        logger.info(
            "Đã tắt hmac_mandatory trên %s web widget (setUser anonymous)",
            hmac_off,
        )
    await db.commit()
    return n


def _is_web_widget_inbox(inbox: dict[str, Any]) -> bool:
    ct = str(inbox.get("channel_type") or inbox.get("channel") or "").lower()
    if "webwidget" in ct.replace(" ", "") or "website" in ct:
        return True
    return _inbox_website_token(inbox) is not None


async def ensure_web_widget_hmac_optional_for_anonymous(
    *,
    messaging_account_id: int,
    inboxes_payload: Any,
) -> int:
    """
    Overlay live-chat dùng $chatwoot.setUser(oh_…) không HMAC.
    hmac_mandatory=true → Chatwoot từ chối identifier giả → miss Redis sticky.
    Tắt bắt buộc HMAC trên mọi web widget khi sync inbox.
    """
    patched = 0
    for inbox in _extract_inbox_items(inboxes_payload):
        if not _is_web_widget_inbox(inbox):
            continue
        if inbox.get("hmac_mandatory") is not True:
            continue
        try:
            inbox_id = int(inbox.get("id"))
        except (TypeError, ValueError):
            continue
        res = await chatwoot_client.application_request(
            "PATCH",
            f"/api/v1/accounts/{messaging_account_id}/inboxes/{inbox_id}",
            json_body={"channel": {"hmac_mandatory": False}},
        )
        if res.status_code in (200, 201):
            patched += 1
            logger.info(
                "hmac_mandatory=false inbox=%s account=%s",
                inbox_id,
                messaging_account_id,
            )
        else:
            logger.warning(
                "Không tắt hmac_mandatory inbox=%s status=%s",
                inbox_id,
                res.status_code,
            )
    return patched


async def get_binding_by_website_token(
    db: AsyncSession,
    website_token: str,
) -> MessagingInboxBinding | None:
    token = (website_token or "").strip()
    if not token:
        return None
    q = await db.execute(
        select(MessagingInboxBinding).where(
            MessagingInboxBinding.website_token == token,
            MessagingInboxBinding.is_active.is_(True),
        )
    )
    return q.scalar_one_or_none()


async def get_binding_by_tenant_inbox(
    db: AsyncSession,
    tenant_id: UUID,
    inbox_id: int,
) -> MessagingInboxBinding | None:
    q = await db.execute(
        select(MessagingInboxBinding).where(
            MessagingInboxBinding.tenant_id == tenant_id,
            MessagingInboxBinding.inbox_id == int(inbox_id),
            MessagingInboxBinding.is_active.is_(True),
        )
    )
    return q.scalar_one_or_none()
