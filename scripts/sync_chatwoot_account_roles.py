#!/usr/bin/env python3
"""
CLI one-shot: sync OmniHub roles → Chatwoot account roles for a tenant.

  admin-partner → administrator
  user / khác   → agent
  + ensure personal Chatwoot API tokens

Usage (from repo root, with env/DB configured):

  python scripts/sync_chatwoot_account_roles.py --tenant-id <UUID>
  python scripts/sync_chatwoot_account_roles.py --all-tenants

Requires edit_users semantics: script runs as system (direct DB), not via JWT.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

# repo root on sys.path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def _run_for_tenant(db, tenant_id: UUID, *, ensure_tokens: bool) -> dict:
    from sqlalchemy import and_, select
    from sqlalchemy.orm import selectinload
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.models import Tenant, User
    from app.services.v1.handle_chatwoot._shared import _resolve_account_id
    from app.services.v1.handle_chatwoot.user_tokens import (
        ensure_user_chatwoot_api_token,
        omnihub_role_is_admin_partner,
        patch_chatwoot_agent_account_role,
        resolve_chatwoot_account_role_for_user,
        resolve_chatwoot_user_id,
        user_has_chatwoot_api_token,
    )

    account_id, _ = await _resolve_account_id(db, tenant_id)
    if account_id is None:
        return {"tenant_id": str(tenant_id), "ok": False, "error": "no_messaging_account"}

    users = (
        await db.execute(
            select(User)
            .options(selectinload(User.role))
            .where(and_(User.tenant_id == tenant_id, User.is_active == 1))
        )
    ).scalars().all()

    updated = 0
    failed = 0
    for u in users:
        desired = await resolve_chatwoot_account_role_for_user(db, u)
        cw_id = await resolve_chatwoot_user_id(db, u)
        if cw_id is None:
            print(f"  skip {u.username}: no chatwoot map")
            continue
        ok, status, _ = await patch_chatwoot_agent_account_role(
            account_id=int(account_id),
            chatwoot_user_id=int(cw_id),
            role=desired,
        )
        if not ok:
            failed += 1
            print(f"  FAIL {u.username} → {desired} (status={status})")
            continue
        updated += 1
        if ensure_tokens and not user_has_chatwoot_api_token(u):
            await ensure_user_chatwoot_api_token(db, u)
        md = dict(u.meta_data) if isinstance(u.meta_data, dict) else {}
        snap = md.get("chatwoot_agent") if isinstance(md.get("chatwoot_agent"), dict) else {}
        md["chatwoot_agent"] = {**snap, "role": desired}
        u.meta_data = md
        flag_modified(u, "meta_data")
        partner = omnihub_role_is_admin_partner(u.role.name if u.role else None)
        print(
            f"  ok {u.username}: omni={u.role.name if u.role else None} "
            f"→ cw={desired} partner={partner}"
        )

    await db.commit()
    return {
        "tenant_id": str(tenant_id),
        "account_id": int(account_id),
        "ok": True,
        "updated": updated,
        "failed": failed,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--tenant-id", type=UUID, help="Một tenant UUID")
    g.add_argument("--all-tenants", action="store_true", help="Mọi tenant có messaging map")
    parser.add_argument(
        "--no-tokens",
        action="store_true",
        help="Không ensure personal API token",
    )
    args = parser.parse_args()

    from app.core.config.database import async_session_maker
    from app.db.models import Tenant
    from sqlalchemy import select

    ensure_tokens = not args.no_tokens
    async with async_session_maker() as db:
        if args.all_tenants:
            tenants = (await db.execute(select(Tenant))).scalars().all()
            ids = [t.id for t in tenants]
        else:
            ids = [args.tenant_id]

        summary = []
        for tid in ids:
            print(f"\n=== tenant {tid} ===")
            summary.append(await _run_for_tenant(db, tid, ensure_tokens=ensure_tokens))

    print("\n--- summary ---")
    for row in summary:
        print(row)
    return 0 if all(r.get("ok") and r.get("failed", 0) == 0 for r in summary) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
