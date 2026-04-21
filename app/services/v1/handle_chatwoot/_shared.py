from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import and_, delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ChatwootLegacyMap,
    ChatwootMapResourceType,
    User,
    generate_uuid7,
)
from app.integrations.chatwoot import client as chatwoot_client
from app.integrations.chatwoot.client import ChatwootResult
from app.schemas.requests.chatwoot import (
    ChatwootAgentBotCreateBody,
    ChatwootAgentBotUpdateBody,
    ChatwootAgentCreateBody,
    ChatwootAgentUpdateBody,
    ChatwootProvisionAccountBody,
    ChatwootUpdateAccountBody,
)
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)
from app.core.config.app_config import settings
from app.utils.helpers import isCheckMaxLevel

logger = logging.getLogger(__name__)

_INTEGRATION_ACCOUNT_USER_ROLE = "administrator"

def _is_tenant_member(current_user: User, tenant_id: UUID) -> bool:
    try:
        return (
            current_user.tenant_id is not None
            and UUID(str(current_user.tenant_id)) == tenant_id
        )
    except Exception:
        return False


async def _require_tenant_access(
    current_user: User, tenant_id: UUID, db: AsyncSession
) -> dict[str, Any] | None:
    """
    Cho phép:
    - Admin (max level) truy cập mọi tenant
    - User thường chỉ truy cập đúng tenant của họ
    """
    if await isCheckMaxLevel(current_user, db):
        return None
    if _is_tenant_member(current_user, tenant_id):
        return None
    return api_response(
        ResponseStatus.ERROR,
        ResponseStatusCode.FORBIDDEN,
        "Bạn không có quyền truy cập tenant này",
    )


def _application_account_path(account_id: int, suffix: str) -> str:
    s = suffix if suffix.startswith("/") else f"/{suffix}"
    return f"/api/v1/accounts/{account_id}{s}"


def _application_error_http_status(code: int) -> int:
    if code in (400, 401, 403, 404, 409, 422, 503):
        return code
    return 502


async def _tenant_application_forward(
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
    *,
    request: Request | None = None,
    method: str,
    path_suffix: str,
    json_body: dict[str, Any] | None = None,
    params: list[tuple[str, str]] | None = None,
    forward_all_query_params: bool = False,
    allowed_query_params: frozenset[str] | None = None,
    redact_agents: bool = True,
    ok_message: str,
    success_codes: frozenset[int] = frozenset({200}),
    extra_response: dict[str, Any] | None = None,
    error_message: str = "Chatwoot trả lỗi",
    error_payload_keys: list[str] | None = None,
) -> Any:
    """Forward Application API theo account đã map; bọc `chatwoot` + optional redact agent id."""
    try:
        if params is None and request is not None:
            if forward_all_query_params:
                params = _forward_all_query_pairs(request)
            elif allowed_query_params is not None:
                params = _forward_query_pairs(request, allowed_query_params)
        denied = await _require_tenant_access(current_user, tenant_id, db)
        if denied is not None:
            return denied
        account_id, _ = await _resolve_account_id(db, tenant_id)
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Chưa có map Chatwoot account cho tenant này",
            )
        path = _application_account_path(account_id, path_suffix)
        res = await chatwoot_client.application_request(
            method, path, json_body=json_body, params=params
        )
        if res.status_code in success_codes:
            data: Any = res.data
            if redact_agents and data is not None:
                cw_map = await _chatwoot_agent_id_to_local_map(db, tenant_id)
                data = _walk_redact_agent_refs(data, cw_map)
            payload: dict[str, Any] = {
                "tenant_id": str(tenant_id),
                "chatwoot": data,
            }
            if extra_response:
                payload.update(extra_response)
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                ok_message,
                payload,
            )
        return api_response(
            ResponseStatus.ERROR,
            _application_error_http_status(res.status_code),
            error_message,
            _chatwoot_error_payload(res, sent_payload_keys=error_payload_keys),
        )
    except SQLAlchemyError as e:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi CSDL: {e}",
        )
    except Exception as e:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi không xác định: {e}",
        )


def _chatwoot_error_payload(
    res: ChatwootResult,
    *,
    sent_payload_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Trả về chi tiết lỗi từ Chatwoot (JSON parse + body thô) để debug 500."""
    out: dict[str, Any] = {
        "chatwoot_http_status": res.status_code,
        "chatwoot_path": res.path,
    }
    if sent_payload_keys is not None:
        out["request_payload_keys_sent"] = sent_payload_keys
    if isinstance(res.data, (dict, list)):
        out["chatwoot_json"] = res.data
    elif res.data is not None:
        out["chatwoot_body"] = res.data
    if res.raw_text:
        out["raw_response_body_preview"] = res.raw_text[:12000]
    return out


def _platform_account_payload_provision(body: ChatwootProvisionAccountBody) -> dict[str, object]:
    """Đầy đủ trường theo doc + extra; bỏ tenant_id, không gửi key chỉ có None."""
    base = body.model_dump(
        mode="json",
        exclude={"tenant_id"},
        exclude_none=True,
    )
    # extra="allow": gộp cả field không khai báo trước
    return base


def _platform_account_payload_update(body: ChatwootUpdateAccountBody) -> dict[str, object]:
    return body.model_dump(mode="json", exclude_unset=True, exclude_none=True)


def _merge_chatwoot_platform_user_payload(
    meta_data: dict[str, Any] | None,
    core: dict[str, Any],
) -> dict[str, Any]:
    """Gộp meta_data (root + chatwoot_user lồng) rồi merge với core.

    - Root meta_data ghi đè nested `chatwoot_user` (cùng key), tránh snapshot cũ đè mất giá trị root.
    - **core** (body API / default) ghi đè meta cùng key — request body là nguồn rõ ràng nhất.
    - Không gửi key `chatwoot_user` lên Platform API.
    """
    extras: dict[str, Any] = {}
    if isinstance(meta_data, dict):
        flat_nested: dict[str, Any] = {}
        nested = meta_data.get("chatwoot_user")
        if isinstance(nested, dict):
            for k, v in nested.items():
                if v is not None:
                    flat_nested[k] = v
        root_flat: dict[str, Any] = {}
        for k, v in meta_data.items():
            if k == "chatwoot_user":
                continue
            if v is not None:
                root_flat[k] = v
        extras = {**flat_nested, **root_flat}
    merged = {**extras, **core}
    out = {k: v for k, v in merged.items() if v is not None}
    out.pop("chatwoot_user", None)
    return out


def _application_agent_payload(body: ChatwootAgentCreateBody | ChatwootAgentUpdateBody) -> dict[str, object]:
    return body.model_dump(mode="json", exclude_unset=True, exclude_none=True)


def _platform_agent_bot_create_payload(
    body: ChatwootAgentBotCreateBody, account_id: int
) -> dict[str, object]:
    out = body.model_dump(mode="json", exclude_none=True)
    out["account_id"] = account_id
    return out


def _platform_agent_bot_update_payload(body: ChatwootAgentBotUpdateBody) -> dict[str, object]:
    """PATCH tenant: không cho đổi account_id qua proxy (tránh chuyển bot sang account khác)."""
    d = body.model_dump(mode="json", exclude_unset=True, exclude_none=True)
    d.pop("account_id", None)
    return d


def _agent_bot_belongs_to_account(bot: Any, account_id: int) -> bool:
    if not isinstance(bot, dict):
        return False
    aid = bot.get("account_id")
    if aid is None:
        return False
    try:
        return int(aid) == account_id
    except (TypeError, ValueError):
        return False


def _chatwoot_agent_public(agent: dict[str, Any], local_uuid: UUID) -> dict[str, Any]:
    """Ẩn id số Chatwoot khỏi payload trả API; `id` là UUID nội bộ."""
    out = {k: v for k, v in agent.items() if k not in ("id", "account_id")}
    out["id"] = str(local_uuid)
    return out


def _chatwoot_agent_bot_public(bot: dict[str, Any], local_uuid: UUID) -> dict[str, Any]:
    out = {
        k: v
        for k, v in bot.items()
        if k not in ("id", "account_id", "access_token")
    }
    out["id"] = str(local_uuid)
    return out


def _chatwoot_user_public(user: dict[str, Any], local_uuid: UUID) -> dict[str, Any]:
    out = {k: v for k, v in user.items() if k != "id"}
    out["id"] = str(local_uuid)
    return out


async def _chatwoot_agent_id_to_local_map(
    db: AsyncSession, tenant_id: UUID
) -> dict[int, UUID]:
    """Map id số agent trên Chatwoot → UUID nội bộ (để che id khi trả JSON)."""
    q = await db.execute(
        select(ChatwootLegacyMap.chatwoot_id, ChatwootLegacyMap.local_uuid).where(
            and_(
                ChatwootLegacyMap.resource_type == ChatwootMapResourceType.AGENT,
                ChatwootLegacyMap.tenant_id == tenant_id,
            )
        )
    )

    return {int(r.chatwoot_id): r.local_uuid for r in q.all()}


def _redact_chatwoot_agent_like_user(
    obj: dict[str, Any], cw_to_local: dict[int, UUID]
) -> dict[str, Any]:
    out = {k: v for k, v in obj.items() if k not in ("access_token", "pubsub_token")}
    out.pop("account_id", None)
    aid = out.get("id")
    if isinstance(aid, int) and aid in cw_to_local:
        out["id"] = str(cw_to_local[aid])
    return out


def _walk_redact_agent_refs(
    obj: Any, cw_to_local: dict[int, UUID]
) -> Any:
    """Thay id agent Chatwoot (assignee, sender agent) bằng UUID map khi có."""
    if isinstance(obj, dict):
        d: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "assignee" and isinstance(v, dict):
                d[k] = _redact_chatwoot_agent_like_user(v, cw_to_local)
            elif (
                k == "sender"
                and obj.get("sender_type") == "agent"
                and isinstance(v, dict)
            ):
                d[k] = _redact_chatwoot_agent_like_user(v, cw_to_local)
            else:
                d[k] = _walk_redact_agent_refs(v, cw_to_local)
        d.pop("account_id", None)
        return d
    if isinstance(obj, list):
        return [_walk_redact_agent_refs(x, cw_to_local) for x in obj]
    return obj


_CONV_LIST_QUERY_KEYS = frozenset(
    {"assignee_type", "status", "q", "inbox_id", "team_id", "page", "labels"}
)
_CONV_MSG_QUERY_KEYS = frozenset({"after", "before"})


def _agents_payload_as_list(data: Any) -> list[Any] | None:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        p = data.get("payload")
        if isinstance(p, list):
            return p
    return None


async def _ensure_tenant_agent_map(
    db: AsyncSession, tenant_id: UUID, chatwoot_numeric_id: int
) -> ChatwootLegacyMap:
    q = await db.execute(
        select(ChatwootLegacyMap).where(
            and_(
                ChatwootLegacyMap.resource_type == ChatwootMapResourceType.AGENT,
                ChatwootLegacyMap.tenant_id == tenant_id,
                ChatwootLegacyMap.chatwoot_id == chatwoot_numeric_id,
            )
        )
    )
    row = q.scalar_one_or_none()
    if row:
        return row
    row = ChatwootLegacyMap(
        resource_type=ChatwootMapResourceType.AGENT,
        local_uuid=generate_uuid7(),
        chatwoot_id=chatwoot_numeric_id,
        tenant_id=tenant_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.flush()
    return row


async def _ensure_tenant_agent_bot_map(
    db: AsyncSession, tenant_id: UUID, chatwoot_numeric_id: int
) -> ChatwootLegacyMap:
    q = await db.execute(
        select(ChatwootLegacyMap).where(
            and_(
                ChatwootLegacyMap.resource_type == ChatwootMapResourceType.AGENT_BOT,
                ChatwootLegacyMap.tenant_id == tenant_id,
                ChatwootLegacyMap.chatwoot_id == chatwoot_numeric_id,
            )
        )
    )
    row = q.scalar_one_or_none()
    if row:
        return row
    row = ChatwootLegacyMap(
        resource_type=ChatwootMapResourceType.AGENT_BOT,
        local_uuid=generate_uuid7(),
        chatwoot_id=chatwoot_numeric_id,
        tenant_id=tenant_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.flush()
    return row


async def _map_tenant_agent_by_local(
    db: AsyncSession, tenant_id: UUID, local_id: UUID
) -> ChatwootLegacyMap | None:
    q = await db.execute(
        select(ChatwootLegacyMap).where(
            and_(
                ChatwootLegacyMap.resource_type == ChatwootMapResourceType.AGENT,
                ChatwootLegacyMap.tenant_id == tenant_id,
                ChatwootLegacyMap.local_uuid == local_id,
            )
        )
    )
    return q.scalar_one_or_none()


async def _map_tenant_agent_bot_by_local(
    db: AsyncSession, tenant_id: UUID, local_id: UUID
) -> ChatwootLegacyMap | None:
    q = await db.execute(
        select(ChatwootLegacyMap).where(
            and_(
                ChatwootLegacyMap.resource_type == ChatwootMapResourceType.AGENT_BOT,
                ChatwootLegacyMap.tenant_id == tenant_id,
                ChatwootLegacyMap.local_uuid == local_id,
            )
        )
    )
    return q.scalar_one_or_none()


async def _map_user_by_local(
    db: AsyncSession, local_user_id: UUID
) -> ChatwootLegacyMap | None:
    q = await db.execute(
        select(ChatwootLegacyMap).where(
            and_(
                ChatwootLegacyMap.resource_type == ChatwootMapResourceType.USER,
                ChatwootLegacyMap.local_uuid == local_user_id,
            )
        )
    )
    return q.scalar_one_or_none()


async def _ensure_user_map(
    db: AsyncSession, local_user_id: UUID, chatwoot_numeric_id: int
) -> ChatwootLegacyMap:
    q = await db.execute(
        select(ChatwootLegacyMap).where(
            and_(
                ChatwootLegacyMap.resource_type == ChatwootMapResourceType.USER,
                ChatwootLegacyMap.local_uuid == local_user_id,
            )
        )
    )
    row = q.scalar_one_or_none()
    if row:
        row.chatwoot_id = chatwoot_numeric_id
        return row
    row = ChatwootLegacyMap(
        resource_type=ChatwootMapResourceType.USER,
        local_uuid=local_user_id,
        chatwoot_id=chatwoot_numeric_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.flush()
    return row


async def _delete_tenant_agent_and_bot_maps(db: AsyncSession, tenant_id: UUID) -> None:
    await db.execute(
        delete(ChatwootLegacyMap).where(
            and_(
                ChatwootLegacyMap.tenant_id == tenant_id,
                ChatwootLegacyMap.resource_type.in_(
                    [
                        ChatwootMapResourceType.AGENT,
                        ChatwootMapResourceType.AGENT_BOT,
                    ]
                ),
            )
        )
    )


async def _integration_chatwoot_user_id() -> tuple[int | None, str | None]:
    """
    User Chatwoot dùng cho Application API (CHATWOOT_USER_API_TOKEN), cần id để POST account_users.
    Ưu tiên CHATWOOT_INTEGRATION_USER_ID; không thì GET /api/v1/profile.
    """
    if settings.CHATWOOT_INTEGRATION_USER_ID is not None:
        return settings.CHATWOOT_INTEGRATION_USER_ID, None
    if not settings.CHATWOOT_USER_API_TOKEN:
        return (
            None,
            "Thiếu CHATWOOT_USER_API_TOKEN và CHATWOOT_INTEGRATION_USER_ID — không thể xác định user để gắn vào account",
        )
    res = await chatwoot_client.application_request("GET", "/api/v1/profile")
    if res.status_code != 200 or not isinstance(res.data, dict):
        return None, "GET /api/v1/profile thất bại — kiểm tra CHATWOOT_USER_API_TOKEN hoặc set CHATWOOT_INTEGRATION_USER_ID"
    uid = res.data.get("id")
    if uid is None:
        return None, "Profile Chatwoot không có trường id"
    try:
        return int(uid), None
    except (TypeError, ValueError):
        return None, f"id profile không hợp lệ: {uid!r}"


async def link_integration_user_to_chatwoot_account(account_id: int) -> dict[str, Any]:
    """
    POST /platform/api/v1/accounts/{id}/account_users — gắn user tích hợp vào account.
    Phía Chatwoot dùng find_or_initialize_by nên gọi lại an toàn (cập nhật role nếu cần).
    """
    uid, skip_reason = await _integration_chatwoot_user_id()
    out: dict[str, Any] = {
        "attempted": False,
        "linked": False,
        "role": _INTEGRATION_ACCOUNT_USER_ROLE,
    }
    if uid is None:
        out["skipped_reason"] = skip_reason
        return out
    out["attempted"] = True
    out["user_id"] = uid
    res = await chatwoot_client.platform_request(
        "POST",
        f"/platform/api/v1/accounts/{account_id}/account_users",
        json_body={"user_id": uid, "role": _INTEGRATION_ACCOUNT_USER_ROLE},
    )
    out["link_http_status"] = res.status_code
    if res.status_code in (200, 201):
        out["linked"] = True
        if isinstance(res.data, dict):
            out["chatwoot_account_user"] = res.data
    else:
        out["error"] = _chatwoot_error_payload(
            res, sent_payload_keys=["user_id", "role"]
        )
        logger.warning(
            "Gắn user tích hợp vào Chatwoot account %s thất bại: HTTP %s",
            account_id,
            res.status_code,
        )
    return out


async def _get_tenant_account_mapping(
    db: AsyncSession, tenant_id: UUID
) -> ChatwootLegacyMap | None:
    q = await db.execute(
        select(ChatwootLegacyMap).where(
            and_(
                ChatwootLegacyMap.resource_type
                == ChatwootMapResourceType.ACCOUNT,
                ChatwootLegacyMap.local_uuid == tenant_id,
            )
        )
    )
    return q.scalar_one_or_none()


async def _resolve_account_id(
    db: AsyncSession, tenant_id: UUID
) -> tuple[int | None, ChatwootLegacyMap | None]:
    m = await _get_tenant_account_mapping(db, tenant_id)
    if not m:
        return None, None
    return m.chatwoot_id, m


def _forward_query_pairs(
    request: Request, allowed: frozenset[str]
) -> list[tuple[str, str]]:
    """Chỉ chuyển tiếp query key được whitelist (tránh forward path tùy ý)."""
    pairs: list[tuple[str, str]] = []
    for key, value in request.query_params.multi_items():
        if key not in allowed or value is None or value == "":
            continue
        pairs.append((key, str(value)))
    return pairs


def _forward_all_query_pairs(request: Request) -> list[tuple[str, str]]:
    """Forward toàn bộ query params không rỗng."""
    pairs: list[tuple[str, str]] = []
    for key, value in request.query_params.multi_items():
        if value is None or value == "":
            continue
        pairs.append((key, str(value)))
    return pairs
