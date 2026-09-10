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


def normalize_omnihub_role_name(name: str | None) -> str:
    """Chuẩn hóa tên role OmniHub để so khớp (admin-partner / admin_partner / …)."""
    return (name or "").strip().lower().replace("_", "-").replace(" ", "-")


def omnihub_role_is_admin_partner(name: str | None) -> bool:
    n = normalize_omnihub_role_name(name)
    if not n:
        return False
    return n == "admin-partner" or "admin-partner" in n


def resolve_chatwoot_account_role(omnihub_role_name: str | None) -> str:
    """
    Map OmniHub role → Chatwoot account role.

    admin-partner → administrator (full trong đúng 1 account/tenant).
    Các role khác → agent (ACL theo inbox).
    """
    if omnihub_role_is_admin_partner(omnihub_role_name):
        return "administrator"
    return "agent"


async def resolve_chatwoot_account_role_for_user(
    db: AsyncSession,
    user: User,
    *,
    role_id: UUID | None = None,
) -> str:
    """Lấy Chatwoot account role từ role OmniHub của user (hoặc role_id sắp gán)."""
    from app.db.models import Role

    rid = role_id if role_id is not None else getattr(user, "role_id", None)
    if rid is not None:
        role = await db.get(Role, rid)
        if role is not None and (role.name or "").strip():
            return resolve_chatwoot_account_role(role.name)
    rel = getattr(user, "role", None)
    if rel is not None and (getattr(rel, "name", None) or "").strip():
        return resolve_chatwoot_account_role(rel.name)
    return "agent"


async def patch_chatwoot_agent_account_role(
    *,
    account_id: int,
    chatwoot_user_id: int,
    role: str,
    access_token: str | None = None,
) -> tuple[bool, int, Any]:
    """
    Đồng bộ Chatwoot account role (agent|administrator).

    Ưu tiên Application PATCH /agents/{id}; fallback Platform POST account_users.
    """
    role_norm = (role or "agent").strip().lower()
    if role_norm not in _CHATWOOT_ACCOUNT_ROLES:
        role_norm = "agent"

    app_res = await chatwoot_client.application_request(
        "PATCH",
        f"/api/v1/accounts/{int(account_id)}/agents/{int(chatwoot_user_id)}",
        json_body={"role": role_norm},
        access_token=access_token,
    )
    if app_res.status_code in (200, 201):
        return True, int(app_res.status_code), app_res.data

    link_res = await chatwoot_client.platform_request(
        "POST",
        f"/platform/api/v1/accounts/{int(account_id)}/account_users",
        json_body={"user_id": int(chatwoot_user_id), "role": role_norm},
    )
    if link_res.status_code in (200, 201):
        return True, int(link_res.status_code), link_res.data

    logger.warning(
        "PATCH Chatwoot role thất bại account=%s user=%s role=%s app=%s platform=%s",
        account_id,
        chatwoot_user_id,
        role_norm,
        app_res.status_code,
        link_res.status_code,
    )
    return False, int(link_res.status_code or app_res.status_code or 502), {
        "application": {"status": app_res.status_code, "data": app_res.data},
        "platform": {"status": link_res.status_code, "data": link_res.data},
    }


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
            "Tài khoản chưa sẵn sàng dùng trò chuyện. "
            "Vui lòng liên hệ quản trị viên để kích hoạt."
        ),
        {
            "code": "chatwoot_api_token_required",
            "meta_key": CHATWOOT_API_TOKEN_META_KEY,
            "bulk_endpoint": "POST /api/v1/user/sync-chatwoot-api-tokens",
        },
    )


async def user_is_elevated_messaging_admin(
    db: AsyncSession, current_user: User
) -> bool:
    """
    Platform admin / admin-partner → phạm vi messaging **full trong tenant**
    (báo cáo, list hội thoại không clamp inbox agent).

    Không đồng nghĩa phải dùng CHATWOOT_USER_API_TOKEN — partner dùng token
    cá nhân sau khi đã là Chatwoot administrator trên account tenant.
    Không dùng level cao nhất tenant để escalate (agent max-level vẫn ACL inbox).
    """
    from app.utils.helpers import is_platform_admin

    if await is_platform_admin(current_user, db):
        return True
    role_name = ""
    if getattr(current_user, "role", None) is not None:
        role_name = current_user.role.name or ""
    return omnihub_role_is_admin_partner(role_name)


def parse_inbox_ids_from_messaging_payload(payload: Any) -> set[int]:
    """Parse id inbox từ GET /inboxes (payload payload/list)."""
    out: set[int] = set()
    raw = payload
    if isinstance(payload, dict):
        raw = payload.get("payload", payload.get("data", payload))
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.add(int(item.get("id")))
        except (TypeError, ValueError):
            continue
    return out


async def fetch_member_inbox_ids(
    db: AsyncSession,
    current_user: User,
    tenant_id: UUID,
    *,
    personal_token: str,
) -> set[int]:
    """Inbox agent là member — GET /inboxes bằng token cá nhân."""
    from app.services.v1.handle_chatwoot._shared import _resolve_account_id

    account_id, _ = await _resolve_account_id(db, tenant_id)
    if account_id is None:
        return set()
    res = await chatwoot_client.application_request(
        "GET",
        f"/api/v1/accounts/{int(account_id)}/inboxes",
        access_token=personal_token,
    )
    if res.status_code != 200:
        return set()
    return parse_inbox_ids_from_messaging_payload(res.data)


async def resolve_agent_scoped_access_token(
    db: AsyncSession,
    current_user: User,
) -> tuple[str | None, Any]:
    """
    Token gọi Application API theo quyền trong tenant.

    - Agent + admin-partner: token **cá nhân** (partner phải là Chatwoot
      administrator trên account tenant để full inbox).
    - Platform admin: ``CHATWOOT_USER_API_TOKEN`` (ops cross-tenant / không
      gắn personal map).
    - Admin-partner thiếu personal token: fallback env (transition) + warning.

    Trả (token, None) hoặc (None, api_error_response).
    """
    from app.core.config.app_config import settings
    from app.utils.helpers import is_platform_admin

    if await is_platform_admin(current_user, db):
        tok = (settings.CHATWOOT_USER_API_TOKEN or "").strip()
        if not tok:
            return None, missing_token_api_response()
        return tok, None

    tok = await ensure_user_chatwoot_api_token(db, current_user)
    if tok:
        return tok, None

    if await user_is_elevated_messaging_admin(db, current_user):
        env_tok = (settings.CHATWOOT_USER_API_TOKEN or "").strip()
        if env_tok:
            logger.warning(
                "admin-partner user=%s thiếu personal Chatwoot token — "
                "fallback CHATWOOT_USER_API_TOKEN (chạy sync role/token)",
                getattr(current_user, "id", None),
            )
            return env_tok, None

    return None, missing_token_api_response()


async def resolve_reports_access_token(
    db: AsyncSession,
    current_user: User,
) -> tuple[str | None, Any]:
    """
    Chatwoot Reports API cần quyền Administrator trên account.

    - Platform admin: env admin token.
    - Admin-partner: token cá nhân (đã là administrator trên account);
      thiếu thì fallback env.
    - Agent: env token để gọi API + cần personal/map để OmniHub clamp scope.
    """
    from app.core.config.app_config import settings
    from app.utils.helpers import is_platform_admin

    elevated = await user_is_elevated_messaging_admin(db, current_user)

    if await is_platform_admin(current_user, db):
        tok = (settings.CHATWOOT_USER_API_TOKEN or "").strip()
        if not tok:
            return None, missing_token_api_response()
        return tok, None

    if elevated:
        personal = await ensure_user_chatwoot_api_token(db, current_user)
        if personal:
            return personal, None
        env_tok = (settings.CHATWOOT_USER_API_TOKEN or "").strip()
        if env_tok:
            logger.warning(
                "admin-partner user=%s reports fallback env admin token",
                getattr(current_user, "id", None),
            )
            return env_tok, None
        return None, missing_token_api_response()

    # Agent: Reports API thường cần Administrator → gọi bằng env; clamp ở OmniHub.
    tok = (settings.CHATWOOT_USER_API_TOKEN or "").strip()
    if not tok:
        return None, missing_token_api_response()
    personal = await ensure_user_chatwoot_api_token(db, current_user)
    if not personal:
        return None, missing_token_api_response()
    if await resolve_chatwoot_user_id(db, current_user) is None:
        from app.schemas.responses.api_response_rule import (
            ResponseStatus,
            ResponseStatusCode,
            api_response,
        )

        return None, api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.FORBIDDEN,
            "Tài khoản chưa sẵn sàng xem báo cáo. Vui lòng liên hệ quản trị viên.",
            {"code": "chatwoot_user_map_required"},
        )
    return tok, None



async def deny_unless_can_assign_assignee(
    db: AsyncSession,
    current_user: User,
    *,
    target_chatwoot_user_id: int | None,
    assigning_team: bool = False,
) -> Any:
    """
    RBAC gán hội thoại:

    - Có ``reassign_messaging_conversation``: không clamp OmniHub —
      Chatwoot tự enforce (inbox membership / role agent).
    - Chỉ có ``assign_messaging_conversation``: chỉ self-assign.

    Trả api_response lỗi hoặc None nếu OK.
    """
    from app.core.security.permissions import get_user_permissions
    from app.schemas.responses.api_response_rule import (
        ResponseStatus,
        ResponseStatusCode,
        api_response,
    )

    perms = await get_user_permissions(current_user.id, db)
    if "reassign_messaging_conversation" in perms:
        return None

    if assigning_team and target_chatwoot_user_id is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.FORBIDDEN,
            "Bạn chỉ được tự nhận hội thoại. Gán theo nhóm cần quyền quản lý phân công.",
            {"code": "reassign_messaging_conversation_required"},
        )

    if target_chatwoot_user_id is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.FORBIDDEN,
            "Bạn chỉ được tự nhận hội thoại cho mình.",
            {"code": "reassign_messaging_conversation_required"},
        )

    self_id = await resolve_chatwoot_user_id(db, current_user)
    if self_id is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.FORBIDDEN,
            "Tài khoản chưa liên kết kênh trò chuyện — không thể tự nhận hội thoại.",
            {"code": "chatwoot_user_map_required"},
        )

    try:
        target = int(target_chatwoot_user_id)
    except (TypeError, ValueError):
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.BAD_REQUEST,
            "Nhân viên được chọn không hợp lệ.",
        )

    if target != int(self_id):
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.FORBIDDEN,
            "Bạn chỉ được tự nhận hội thoại, không được gán cho nhân viên khác.",
            {
                "code": "reassign_messaging_conversation_required",
                "allowed_assignee": "self",
            },
        )
    return None


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
