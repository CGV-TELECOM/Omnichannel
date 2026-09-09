"""Chatwoot Application API token theo OmniHub user (sender = chủ token).

Lưu 1 lần: users.meta_data.chatwoot_api_access_token

Nguồn ưu tiên khi tạo mới:
  Platform POST /users (response access_token) + POST account_users
→ không cần Rails patch PlatformAppPermissible cho user mới.

Fallback / backfill: Platform GET /users/{id} → access_token
(user do Platform App tạo thì GET được trên Chatwoot stock).

Không fallback đổi password. Không silent-fallback CHATWOOT_USER_API_TOKEN khi gửi tin.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import ChatwootLegacyMap, ChatwootMapResourceType, User
from app.integrations.chatwoot import client as chatwoot_client

logger = logging.getLogger(__name__)

CHATWOOT_API_TOKEN_META_KEY = "chatwoot_api_access_token"
HAS_CHATWOOT_API_TOKEN_PUBLIC_KEY = "has_chatwoot_api_access_token"
_BULK_CONCURRENCY = 8
_CHATWOOT_ACCOUNT_ROLES = frozenset({"agent", "administrator"})


def _platform_user_password(preferred: str | None) -> str:
    """Chatwoot Platform thường yêu cầu hoa/thường/số/ký tự đặc biệt."""
    raw = (preferred or "").strip()
    if (
        len(raw) >= 8
        and any(c.isupper() for c in raw)
        and any(c.islower() for c in raw)
        and any(c.isdigit() for c in raw)
        and any(not c.isalnum() for c in raw)
    ):
        return raw
    return f"Aa1!{secrets.token_urlsafe(24)}"


@dataclass(slots=True)
class PlatformAgentProvision:
    ok: bool
    chatwoot_user_id: int | None = None
    access_token: str | None = None
    status_code: int = 0
    error_data: Any = None
    role: str = "agent"


async def provision_account_agent_via_platform(
    *,
    account_id: int,
    name: str,
    email: str,
    password: str | None = None,
    display_name: str | None = None,
    role: str = "agent",
    availability: str | None = None,
    auto_offline: bool | None = None,
) -> PlatformAgentProvision:
    """
    Tạo (hoặc tái sử dụng email) user qua Platform API, gắn vào account, lấy access_token.

    Stock Chatwoot: create user tự gắn PlatformAppPermissible → không cần patch Rails.
    Account phải đã permissible với Platform App (account tạo qua Platform / đã grant).
    """
    role_norm = (role or "agent").strip().lower()
    if role_norm not in _CHATWOOT_ACCOUNT_ROLES:
        role_norm = "agent"

    body: dict[str, Any] = {
        "name": (name or "").strip() or email,
        "email": (email or "").strip(),
        "password": _platform_user_password(password),
    }
    dn = (display_name or "").strip()
    if dn:
        body["display_name"] = dn

    create_res = await chatwoot_client.platform_request(
        "POST", "/platform/api/v1/users", json_body=body
    )
    if create_res.status_code not in (200, 201) or not isinstance(create_res.data, dict):
        return PlatformAgentProvision(
            ok=False,
            status_code=create_res.status_code or 502,
            error_data=create_res.data,
            role=role_norm,
        )
    try:
        cw_id = int(create_res.data["id"])
    except (TypeError, ValueError, KeyError):
        return PlatformAgentProvision(
            ok=False,
            status_code=502,
            error_data=create_res.data,
            role=role_norm,
        )

    tok = (create_res.data.get("access_token") or "").strip()[:512] or None

    link_res = await chatwoot_client.platform_request(
        "POST",
        f"/platform/api/v1/accounts/{int(account_id)}/account_users",
        json_body={"user_id": cw_id, "role": role_norm},
    )
    if link_res.status_code not in (200, 201):
        # Chỉ gỡ membership — không xóa user (email có thể đã tồn tại trước đó).
        try:
            await chatwoot_client.platform_request(
                "DELETE",
                f"/platform/api/v1/accounts/{int(account_id)}/account_users",
                json_body={"user_id": cw_id},
            )
        except Exception:
            logger.exception(
                "Compensation unlink account_user thất bại account=%s user=%s",
                account_id,
                cw_id,
            )
        return PlatformAgentProvision(
            ok=False,
            chatwoot_user_id=cw_id,
            status_code=link_res.status_code or 502,
            error_data=link_res.data,
            role=role_norm,
        )

    if availability is not None or auto_offline is not None:
        patch_body: dict[str, Any] = {}
        if availability is not None:
            patch_body["availability"] = availability
        if auto_offline is not None:
            patch_body["auto_offline"] = auto_offline
        try:
            await chatwoot_client.application_request(
                "PATCH",
                f"/api/v1/accounts/{int(account_id)}/agents/{cw_id}",
                json_body=patch_body,
            )
        except Exception:
            logger.exception(
                "Best-effort PATCH availability agent=%s account=%s thất bại",
                cw_id,
                account_id,
            )

    if not tok:
        tok = await try_fetch_platform_user_access_token(cw_id)

    return PlatformAgentProvision(
        ok=True,
        chatwoot_user_id=cw_id,
        access_token=tok,
        status_code=200,
        role=role_norm,
    )


USER_INBOX_IDS_META_KEY = "chatwoot_inbox_ids"
TENANT_DEFAULT_INBOX_IDS_META_KEY = "default_messaging_inbox_ids"


def collect_messaging_inbox_ids_for_assign(
    *meta_sources: dict[str, Any] | None,
) -> list[int]:
    """Gộp inbox id từ user.meta / tenant.meta (chatwoot_inbox_ids | default_messaging_inbox_ids)."""
    seen: set[int] = set()
    ordered: list[int] = []
    keys = (USER_INBOX_IDS_META_KEY, TENANT_DEFAULT_INBOX_IDS_META_KEY)
    for meta in meta_sources:
        if not isinstance(meta, dict):
            continue
        for key in keys:
            raw = meta.get(key)
            if raw is None:
                continue
            items = raw if isinstance(raw, (list, tuple)) else [raw]
            for item in items:
                try:
                    iid = int(item)
                except (TypeError, ValueError):
                    continue
                if iid <= 0 or iid in seen:
                    continue
                seen.add(iid)
                ordered.append(iid)
    return ordered


async def assign_chatwoot_agent_to_inboxes(
    *,
    account_id: int,
    chatwoot_user_id: int,
    inbox_ids: list[int],
) -> dict[str, Any]:
    """Best-effort POST inbox_members (Application admin token) cho từng inbox."""
    ok: list[int] = []
    failed: list[dict[str, Any]] = []
    for iid in inbox_ids:
        try:
            res = await chatwoot_client.application_request(
                "POST",
                f"/api/v1/accounts/{int(account_id)}/inbox_members",
                json_body={
                    "inbox_id": int(iid),
                    "user_ids": [int(chatwoot_user_id)],
                },
            )
        except Exception as exc:
            failed.append({"inbox_id": iid, "error": str(exc)[:200]})
            continue
        if res.status_code in (200, 201):
            ok.append(int(iid))
        else:
            failed.append(
                {
                    "inbox_id": iid,
                    "status_code": res.status_code,
                    "response": res.data,
                }
            )
    return {
        "assigned": ok,
        "failed": failed,
        "requested": list(inbox_ids),
    }


async def recover_platform_access_token_for_user(
    user: User,
    chatwoot_user_id: int,
) -> str | None:
    """
    Lấy token: Platform GET trước; nếu 401/thiếu (user cũ Application API) →
    Platform POST /users theo email để grant PlatformAppPermissible rồi lấy token.
    Không cần Rails patch.
    """
    tok = await try_fetch_platform_user_access_token(chatwoot_user_id)
    if tok:
        return tok

    email = (user.email or "").strip()
    if not email:
        return None

    body: dict[str, Any] = {
        "name": (user.fullname or user.username or email).strip(),
        "email": email,
        "password": _platform_user_password(None),
    }
    try:
        res = await chatwoot_client.platform_request(
            "POST", "/platform/api/v1/users", json_body=body
        )
    except Exception:
        logger.exception(
            "Platform re-touch user thất bại cw_user_id=%s", chatwoot_user_id
        )
        return None

    if res.status_code in (200, 201) and isinstance(res.data, dict):
        tok = (res.data.get("access_token") or "").strip()[:512] or None
        if tok:
            return tok
        # id có thể đổi? thường cùng email → cùng id
        try:
            returned_id = int(res.data.get("id") or chatwoot_user_id)
        except (TypeError, ValueError):
            returned_id = chatwoot_user_id
        return await try_fetch_platform_user_access_token(returned_id)

    logger.info(
        "Platform re-touch cw_user_id=%s status=%s",
        chatwoot_user_id,
        res.status_code,
    )
    return None


def get_user_chatwoot_api_token(user: User | None) -> str | None:
    if user is None:
        return None
    meta = user.meta_data if isinstance(user.meta_data, dict) else {}
    tok = (meta.get(CHATWOOT_API_TOKEN_META_KEY) or "").strip()
    return tok[:512] if tok else None


def user_has_chatwoot_api_token(user: User | None) -> bool:
    return bool(get_user_chatwoot_api_token(user))


def set_user_chatwoot_api_token(user: User, token: str | None) -> bool:
    meta = dict(user.meta_data) if isinstance(user.meta_data, dict) else {}
    cleaned = (token or "").strip()[:512] or None
    prev = (meta.get(CHATWOOT_API_TOKEN_META_KEY) or "").strip() or None
    if cleaned == prev:
        return False
    if cleaned:
        meta[CHATWOOT_API_TOKEN_META_KEY] = cleaned
    else:
        meta.pop(CHATWOOT_API_TOKEN_META_KEY, None)
    user.meta_data = meta
    flag_modified(user, "meta_data")
    return True


def redact_chatwoot_token_from_meta(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(meta, dict):
        return None
    out = {k: v for k, v in meta.items() if k != CHATWOOT_API_TOKEN_META_KEY}
    out[HAS_CHATWOOT_API_TOKEN_PUBLIC_KEY] = bool(
        str(meta.get(CHATWOOT_API_TOKEN_META_KEY) or "").strip()
    )
    return out or None


def missing_token_api_response():
    from app.schemas.responses.api_response_rule import (
        ResponseStatus,
        ResponseStatusCode,
        api_response,
    )

    return api_response(
        ResponseStatus.ERROR,
        ResponseStatusCode.FORBIDDEN,
        (
            "Tài khoản chưa có Chatwoot API token. "
            "Gọi POST /api/v1/user/me/ensure-chatwoot-api-token hoặc "
            "POST /api/v1/user/sync-chatwoot-api-tokens (Platform GET lưu 1 lần)."
        ),
        {
            "code": "chatwoot_api_token_required",
            "meta_key": CHATWOOT_API_TOKEN_META_KEY,
            "bulk_endpoint": "POST /api/v1/user/sync-chatwoot-api-tokens",
        },
    )


async def resolve_agent_scoped_access_token(
    db: AsyncSession,
    current_user: User,
) -> tuple[str | None, Any]:
    """
    Token gọi Application API theo quyền trong tenant.

    - Agent thường: token cá nhân (Chatwoot enforce inbox ACL).
    - Platform admin, admin-partner, hoặc level cao nhất trong tenant:
      CHATWOOT_USER_API_TOKEN — quản trị full account messaging của tenant.

    Trả (token, None) hoặc (None, api_error_response).
    """
    from app.core.config.app_config import settings
    from app.utils.helpers import is_platform_admin, isCheckMaxLevelTenant

    elevated = await is_platform_admin(current_user, db)
    if not elevated:
        role_name = ""
        if getattr(current_user, "role", None) is not None:
            role_name = (current_user.role.name or "").strip().lower()
        if role_name in {"admin-partner", "admin_partner", "admin partner"}:
            elevated = True
        elif "admin-partner" in role_name.replace("_", "-"):
            elevated = True
    if not elevated:
        try:
            elevated = bool(
                current_user.level is not None
                and await isCheckMaxLevelTenant(current_user, db)
            )
        except Exception:
            elevated = False

    if elevated:
        tok = (settings.CHATWOOT_USER_API_TOKEN or "").strip()
        if not tok:
            return None, missing_token_api_response()
        return tok, None

    tok = await ensure_user_chatwoot_api_token(db, current_user)
    if not tok:
        return None, missing_token_api_response()
    return tok, None



async def resolve_chatwoot_user_id(db: AsyncSession, user: User) -> int | None:
    stmt = select(ChatwootLegacyMap).where(
        and_(
            ChatwootLegacyMap.resource_type == ChatwootMapResourceType.USER,
            ChatwootLegacyMap.local_uuid == user.id,
        )
    )
    m = (await db.execute(stmt)).scalar_one_or_none()
    if m is not None and m.chatwoot_id is not None:
        try:
            return int(m.chatwoot_id)
        except (TypeError, ValueError):
            pass
    if user.chat_id is not None:
        try:
            return int(user.chat_id)
        except (TypeError, ValueError):
            return None
    return None


async def try_fetch_platform_user_access_token(chatwoot_user_id: int) -> str | None:
    """Platform GET user → access_token (không log token)."""
    try:
        res = await chatwoot_client.platform_request(
            "GET", f"/platform/api/v1/users/{int(chatwoot_user_id)}"
        )
    except Exception:
        logger.exception(
            "Platform GET user thất bại cw_user_id=%s", chatwoot_user_id
        )
        return None
    if res.status_code != 200 or not isinstance(res.data, dict):
        err = ""
        if isinstance(res.data, dict):
            err = str(res.data.get("error") or "")[:120]
        logger.info(
            "Platform GET user cw_user_id=%s status=%s err=%s",
            chatwoot_user_id,
            res.status_code,
            err,
        )
        return None
    tok = (res.data.get("access_token") or "").strip()
    return tok[:512] if tok else None


async def ensure_user_chatwoot_api_token(
    db: AsyncSession,
    user: User,
    *,
    force_refresh: bool = False,
) -> str | None:
    """Đã lưu → trả về; thiếu → Platform GET → lưu 1 lần."""
    if not force_refresh:
        existing = get_user_chatwoot_api_token(user)
        if existing:
            return existing

    cw_id = await resolve_chatwoot_user_id(db, user)
    if cw_id is None:
        logger.info("ensure_token: user=%s chưa map messaging", user.id)
        return None

    tok = await recover_platform_access_token_for_user(user, cw_id)
    if not tok:
        return None

    set_user_chatwoot_api_token(user, tok)
    try:
        await db.commit()
        await db.refresh(user)
    except Exception:
        await db.rollback()
        logger.exception(
            "Commit chatwoot_api_access_token thất bại user=%s", user.id
        )
        return None
    return get_user_chatwoot_api_token(user)


async def capture_token_into_user_meta(
    user: User, chatwoot_user_id: int
) -> bool:
    """Gọi trước commit create/sync — ghi token vào meta nếu lấy được (chưa commit)."""
    if user_has_chatwoot_api_token(user):
        return True
    tok = await recover_platform_access_token_for_user(user, chatwoot_user_id)
    if not tok:
        return False
    set_user_chatwoot_api_token(user, tok)
    return True


async def bulk_backfill_tenant_chatwoot_api_tokens(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    only_missing: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Backfill token cả tenant — GET; thiếu thì Platform re-touch theo email."""
    stmt = select(User).where(User.tenant_id == tenant_id, User.is_active != 0)
    users = list((await db.execute(stmt)).scalars().all())

    skipped_has_token = 0
    skipped_no_map = 0
    captured = 0
    failed: list[dict[str, Any]] = []
    pending: list[tuple[User, int]] = []

    for u in users:
        if only_missing and not force_refresh and user_has_chatwoot_api_token(u):
            skipped_has_token += 1
            continue
        cw_id = await resolve_chatwoot_user_id(db, u)
        if cw_id is None:
            skipped_no_map += 1
            continue
        pending.append((u, cw_id))

    sem = asyncio.Semaphore(_BULK_CONCURRENCY)

    async def _fetch(u: User, cw_id: int) -> tuple[int, str | None]:
        async with sem:
            return cw_id, await recover_platform_access_token_for_user(u, cw_id)

    fetched = await asyncio.gather(*[_fetch(u, cw_id) for u, cw_id in pending])
    token_by_cw = {cw_id: tok for cw_id, tok in fetched}

    for u, cw_id in pending:
        tok = token_by_cw.get(cw_id)
        if not tok:
            failed.append(
                {
                    "user_id": str(u.id),
                    "username": u.username,
                    "chatwoot_user_id": cw_id,
                    "reason": "platform_no_access_token",
                }
            )
            continue
        set_user_chatwoot_api_token(u, tok)
        captured += 1

    if captured:
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception(
                "Bulk commit chatwoot_api_access_token thất bại tenant=%s",
                tenant_id,
            )
            return {
                "tenant_id": str(tenant_id),
                "total_users": len(users),
                "captured": 0,
                "skipped_has_token": skipped_has_token,
                "skipped_no_map": skipped_no_map,
                "failed_count": len(failed) + 1,
                "failed": failed[:100]
                + [{"reason": "db_commit_failed", "attempted_capture": captured}],
                "ok": False,
            }

    return {
        "tenant_id": str(tenant_id),
        "total_users": len(users),
        "pending": len(pending),
        "captured": captured,
        "skipped_has_token": skipped_has_token,
        "skipped_no_map": skipped_no_map,
        "failed_count": len(failed),
        "failed": failed[:100],
        "ok": True,
    }
