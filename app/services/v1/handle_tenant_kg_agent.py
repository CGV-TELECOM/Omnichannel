"""Tenant ↔ KG Core agents (relational)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant, TenantKgAgent
from app.schemas.requests.tenant import TenantKgAgentInput, TenantKgAgentResponse


class KgAgentSyncError(ValueError):
    """Lỗi đồng bộ kg_agents (400 cho client)."""


def _normalize_kg_agent_entries(
    entries: list[TenantKgAgentInput],
) -> list[TenantKgAgentInput]:
    if not entries:
        return []
    normalized: list[TenantKgAgentInput] = []
    seen_keys: set[str] = set()
    for raw in entries:
        key = (raw.key or "default").strip() or "default"
        if key in seen_keys:
            raise ValueError(f"Trùng key KG agent: {key}")
        seen_keys.add(key)
        normalized.append(raw.model_copy(update={"key": key[:64]}))
    if not any(e.is_default for e in normalized):
        normalized[0] = normalized[0].model_copy(update={"is_default": True})
    elif sum(1 for e in normalized if e.is_default) > 1:
        seen_default = False
        fixed: list[TenantKgAgentInput] = []
        for e in normalized:
            if e.is_default:
                if seen_default:
                    fixed.append(e.model_copy(update={"is_default": False}))
                else:
                    fixed.append(e)
                    seen_default = True
            else:
                fixed.append(e)
        normalized = fixed
    return normalized


def prune_messaging_bot_kg_refs(
    meta: dict[str, Any] | None,
    valid_kg_row_ids: set[UUID],
) -> tuple[dict[str, Any], bool]:
    """Xóa tenant_kg_agent_id trên bot nếu row KG không còn tồn tại."""
    from app.services.v1.handle_chatwoot.chatbot import (
        messaging_bots_to_meta_list,
        parse_tenant_messaging_bots,
    )

    base = dict(meta) if isinstance(meta, dict) else {}
    bots = parse_tenant_messaging_bots(base)
    if not bots:
        return base, False
    changed = False
    updated = []
    for bot in bots:
        if (
            bot.tenant_kg_agent_id is not None
            and bot.tenant_kg_agent_id not in valid_kg_row_ids
        ):
            updated.append(bot.model_copy(update={"tenant_kg_agent_id": None}))
            changed = True
        else:
            updated.append(bot)
    if not changed:
        return base, False
    base["messaging_bots"] = messaging_bots_to_meta_list(updated)
    base.pop("messaging_ai_bot_agent_uuid", None)
    return base, True


def clear_messaging_bot_kg_refs_for_ids(
    meta: dict[str, Any] | None,
    removed_row_ids: set[UUID],
) -> tuple[dict[str, Any], bool]:
    """Cascade: bỏ tenant_kg_agent_id khi row KG bị xóa."""
    if not removed_row_ids:
        return dict(meta) if isinstance(meta, dict) else {}, False
    from app.services.v1.handle_chatwoot.chatbot import (
        messaging_bots_to_meta_list,
        parse_tenant_messaging_bots,
    )

    base = dict(meta) if isinstance(meta, dict) else {}
    bots = parse_tenant_messaging_bots(base)
    if not bots:
        return base, False
    changed = False
    updated = []
    for bot in bots:
        if (
            bot.tenant_kg_agent_id is not None
            and bot.tenant_kg_agent_id in removed_row_ids
        ):
            updated.append(bot.model_copy(update={"tenant_kg_agent_id": None}))
            changed = True
        else:
            updated.append(bot)
    if not changed:
        return base, False
    base["messaging_bots"] = messaging_bots_to_meta_list(updated)
    base.pop("messaging_ai_bot_agent_uuid", None)
    return base, True


async def tenant_has_active_kg_agent(db: AsyncSession, tenant_id: UUID) -> bool:
    q = await db.execute(
        select(func.count())
        .select_from(TenantKgAgent)
        .where(
            TenantKgAgent.tenant_id == tenant_id,
            TenantKgAgent.is_active.is_(True),
        )
    )
    return int(q.scalar() or 0) > 0


async def ensure_graph_activation_has_kg_agents(
    db: AsyncSession,
    tenant: Tenant,
) -> None:
    if int(tenant.graph_activated or 0) != 1:
        return
    if not await tenant_has_active_kg_agent(db, tenant.id):
        raise KgAgentSyncError(
            "graph_activated=1 yêu cầu ít nhất một kg_agents active. "
            "Thêm agent KG hoặc tắt graph_activated."
        )


async def apply_tenant_kg_agents_sync(
    db: AsyncSession,
    tenant: Tenant,
    entries: list[TenantKgAgentInput],
) -> list[TenantKgAgent]:
    """
    Đồng bộ kg_agents + cascade clear messaging_bots.tenant_kg_agent_id orphan.
    Reject kg_agents=[] khi graph_activated=1.
    """
    if int(tenant.graph_activated or 0) == 1 and not entries:
        raise KgAgentSyncError(
            "Không thể xóa hết kg_agents khi graph_activated=1. "
            "Tắt graph_activated hoặc giữ ít nhất một agent KG."
        )

    normalized = _normalize_kg_agent_entries(entries)
    now = datetime.now(timezone.utc)

    existing_q = await db.execute(
        select(TenantKgAgent).where(TenantKgAgent.tenant_id == tenant.id)
    )
    by_key = {row.key: row for row in existing_q.scalars().all()}
    incoming_keys = {e.key for e in normalized}
    removed_row_ids = {
        row.id for key, row in by_key.items() if key not in incoming_keys
    }

    meta = tenant.meta_data if isinstance(tenant.meta_data, dict) else {}
    new_meta, meta_changed = clear_messaging_bot_kg_refs_for_ids(
        meta, removed_row_ids
    )
    if meta_changed:
        tenant.meta_data = new_meta

    for key, row in list(by_key.items()):
        if key not in incoming_keys:
            await db.delete(row)

    result: list[TenantKgAgent] = []
    for entry in normalized:
        row = by_key.get(entry.key)
        if row is None:
            row = TenantKgAgent(
                tenant_id=tenant.id,
                key=entry.key,
                created_at=now,
            )
            db.add(row)
        row.kg_agent_id = entry.kg_agent_id
        row.graph_id = entry.graph_id
        row.inbox_id = entry.inbox_id
        row.label = entry.label
        row.is_default = bool(entry.is_default)
        row.is_active = bool(entry.is_active)
        row.updated_at = now
        result.append(row)

    await db.flush()

    valid_ids = {r.id for r in result}
    meta = tenant.meta_data if isinstance(tenant.meta_data, dict) else {}
    pruned_meta, pruned = prune_messaging_bot_kg_refs(meta, valid_ids)
    if pruned:
        tenant.meta_data = pruned_meta

    await ensure_graph_activation_has_kg_agents(db, tenant)
    return result


async def sync_tenant_kg_agents(
    db: AsyncSession,
    tenant_id: UUID,
    entries: list[TenantKgAgentInput],
) -> list[TenantKgAgent]:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise KgAgentSyncError("Tenant không tồn tại")
    return await apply_tenant_kg_agents_sync(db, tenant, entries)


def kg_agent_row_to_response(row: TenantKgAgent) -> TenantKgAgentResponse:
    return TenantKgAgentResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        kg_agent_id=row.kg_agent_id,
        graph_id=row.graph_id,
        inbox_id=row.inbox_id,
        key=row.key,
        label=row.label,
        is_default=bool(row.is_default),
        is_active=bool(row.is_active),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def load_active_kg_personas(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    inbox_id: int | None = None,
) -> list[TenantKgAgent]:
    """
    Catalog persona active.
    inbox_id set → rows (inbox_id IS NULL OR inbox_id = ?) — ưu tiên đúng inbox khi resolve default.
    """
    rows = await load_tenant_kg_agents(db, tenant_id)
    active = [r for r in rows if r.is_active]
    if inbox_id is None:
        return active
    scoped = [r for r in active if r.inbox_id is None or int(r.inbox_id) == int(inbox_id)]
    return scoped


async def resolve_default_kg_agent_row(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    inbox_id: int | None = None,
) -> TenantKgAgent | None:
    active = await load_active_kg_personas(db, tenant_id, inbox_id=inbox_id)
    if not active:
        return None
    # Ưu tiên default trong scope inbox cụ thể, rồi default chung, rồi single
    inbox_defaults = [
        r
        for r in active
        if r.is_default and inbox_id is not None and r.inbox_id is not None
        and int(r.inbox_id) == int(inbox_id)
    ]
    if inbox_defaults:
        return inbox_defaults[0]
    for row in active:
        if row.is_default:
            return row
    if len(active) == 1:
        return active[0]
    return None



async def load_tenant_kg_agents(
    db: AsyncSession,
    tenant_id: UUID,
) -> list[TenantKgAgent]:
    q = await db.execute(
        select(TenantKgAgent)
        .where(TenantKgAgent.tenant_id == tenant_id)
        .order_by(TenantKgAgent.is_default.desc(), TenantKgAgent.key)
    )
    return list(q.scalars().all())


async def load_kg_agents_map(
    db: AsyncSession,
    tenant_ids: list[UUID],
) -> dict[UUID, list[TenantKgAgent]]:
    if not tenant_ids:
        return {}
    q = await db.execute(
        select(TenantKgAgent)
        .where(TenantKgAgent.tenant_id.in_(tenant_ids))
        .order_by(TenantKgAgent.is_default.desc(), TenantKgAgent.key)
    )
    out: dict[UUID, list[TenantKgAgent]] = {tid: [] for tid in tenant_ids}
    for row in q.scalars().all():
        out.setdefault(row.tenant_id, []).append(row)
    return out


async def resolve_default_kg_agent_id(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    inbox_id: int | None = None,
) -> UUID | None:
    row = await resolve_default_kg_agent_row(db, tenant_id, inbox_id=inbox_id)
    return row.kg_agent_id if row else None


async def resolve_kg_agent_id_by_row_id(
    db: AsyncSession,
    tenant_id: UUID,
    tenant_kg_agent_id: UUID | None,
) -> UUID | None:
    if tenant_kg_agent_id is None:
        return None
    row = await db.get(TenantKgAgent, tenant_kg_agent_id)
    if row is None or row.tenant_id != tenant_id or not row.is_active:
        return None
    return row.kg_agent_id


async def validate_tenant_kg_agent_ids(
    db: AsyncSession,
    tenant_id: UUID,
    row_ids: list[UUID],
) -> str | None:
    if not row_ids:
        return None
    unique_ids = list(dict.fromkeys(row_ids))
    q = await db.execute(
        select(TenantKgAgent.id).where(
            TenantKgAgent.tenant_id == tenant_id,
            TenantKgAgent.id.in_(unique_ids),
            TenantKgAgent.is_active.is_(True),
        )
    )
    found = {r for r in q.scalars().all()}
    missing = [str(i) for i in unique_ids if i not in found]
    if missing:
        return (
            "tenant_kg_agent_id không thuộc tenant hoặc không active: "
            + ", ".join(missing)
        )
    return None


async def build_tenant_kg_agent_payload(
    db: AsyncSession,
    tenant: Tenant,
) -> list[dict[str, Any]]:
    rows = (
        tenant.kg_agents
        if tenant.kg_agents
        else await load_tenant_kg_agents(db, tenant.id)
    )
    return [kg_agent_row_to_response(r).model_dump() for r in rows]
