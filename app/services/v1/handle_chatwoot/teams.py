from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatwootLegacyMap, ChatwootMapResourceType, User, generate_uuid7
from app.integrations.chatwoot import client as chatwoot_client
from app.schemas.requests.chatwoot import (
    ChatwootTeamCreateBody,
    ChatwootTeamUpdateBody,
    ChatwootTeamMembersBody,
)
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)
from app.services.v1.handle_chatwoot._shared import (
    _chatwoot_error_payload,
    _forward_all_query_pairs,
    _require_tenant_access,
    _resolve_account_id,
    _ensure_tenant_agent_map,
    _chatwoot_agent_public,
    _agents_payload_as_list,
)

logger = logging.getLogger(__name__)


async def _ensure_tenant_team_map(
    db: AsyncSession, tenant_id: UUID, chatwoot_numeric_id: int
) -> ChatwootLegacyMap:
    q = await db.execute(
        select(ChatwootLegacyMap).where(
            and_(
                ChatwootLegacyMap.resource_type == ChatwootMapResourceType.TEAM,
                ChatwootLegacyMap.tenant_id == tenant_id,
                ChatwootLegacyMap.chatwoot_id == chatwoot_numeric_id,
            )
        )
    )
    row = q.scalar_one_or_none()
    if row:
        return row
    row = ChatwootLegacyMap(
        resource_type=ChatwootMapResourceType.TEAM,
        local_uuid=generate_uuid7(),
        chatwoot_id=chatwoot_numeric_id,
        tenant_id=tenant_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.flush()
    return row


async def _map_tenant_team_by_local(
    db: AsyncSession, tenant_id: UUID, local_id: UUID
) -> ChatwootLegacyMap | None:
    q = await db.execute(
        select(ChatwootLegacyMap).where(
            and_(
                ChatwootLegacyMap.resource_type == ChatwootMapResourceType.TEAM,
                ChatwootLegacyMap.tenant_id == tenant_id,
                ChatwootLegacyMap.local_uuid == local_id,
            )
        )
    )
    return q.scalar_one_or_none()


def _chatwoot_team_public(team: dict[str, Any], local_uuid: UUID) -> dict[str, Any]:
    out = {k: v for k, v in team.items() if k not in ("id", "account_id")}
    out["id"] = str(local_uuid)
    return out


async def list_teams(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    try:
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

        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.application_request(
            "GET",
            f"/api/v1/accounts/{account_id}/teams",
            params=pairs or None,
        )
        data = res.data
        if res.status_code == 200:
            if not isinstance(data, list):
                return api_response(
                    ResponseStatus.SUCCESS,
                    ResponseStatusCode.OK,
                    "Danh sách team Chatwoot",
                    {"tenant_id": str(tenant_id), "teams": data},
                )
            public_teams = []
            for item in data:
                if not isinstance(item, dict) or item.get("id") is None:
                    public_teams.append(item)
                    continue
                try:
                    cw_id = int(item["id"])
                except (TypeError, ValueError):
                    public_teams.append(item)
                    continue
                m = await _ensure_tenant_team_map(db, tenant_id, cw_id)
                public_teams.append(_chatwoot_team_public(item, m.local_uuid))
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Danh sách team Chatwoot",
                {"tenant_id": str(tenant_id), "teams": public_teams},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 503) else 502,
            "Không lấy được danh sách team từ Chatwoot",
            _chatwoot_error_payload(res),
        )
    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi CSDL: {e}",
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi không xác định: {e}",
        )


async def create_team(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    body: ChatwootTeamCreateBody,
    db: AsyncSession,
):
    try:
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

        payload = body.model_dump(exclude_none=True)
        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.application_request(
            "POST",
            f"/api/v1/accounts/{account_id}/teams",
            json_body=payload,
            params=pairs or None,
        )
        data = res.data
        if res.status_code in (200, 201) and isinstance(data, dict) and data.get("id") is not None:
            try:
                cw_id = int(data["id"])
            except (TypeError, ValueError):
                return api_response(
                    ResponseStatus.ERROR,
                    502,
                    "Chatwoot trả team không có id hợp lệ",
                    _chatwoot_error_payload(res, sent_payload_keys=sorted(payload.keys(), key=str)),
                )
            m = await _ensure_tenant_team_map(db, tenant_id, cw_id)
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã tạo team trên Chatwoot",
                {
                    "tenant_id": str(tenant_id),
                    "team": _chatwoot_team_public(data, m.local_uuid),
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 503) else 502,
            "Tạo team thất bại",
            _chatwoot_error_payload(res, sent_payload_keys=sorted(payload.keys(), key=str)),
        )
    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi CSDL: {e}",
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi không xác định: {e}",
        )


async def get_team(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    team_id: UUID,
    db: AsyncSession,
):
    try:
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

        m = await _map_tenant_team_by_local(db, tenant_id, team_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map Team cho UUID này",
            )

        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.application_request(
            "GET",
            f"/api/v1/accounts/{account_id}/teams/{m.chatwoot_id}",
            params=pairs or None,
        )
        data = res.data
        if res.status_code == 200 and isinstance(data, dict):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Lấy thông tin Team thành công",
                {
                    "tenant_id": str(tenant_id),
                    "team": _chatwoot_team_public(data, m.local_uuid),
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 503) else 502,
            "Không lấy được Team từ Chatwoot",
            _chatwoot_error_payload(res),
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


async def update_team(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    team_id: UUID,
    body: ChatwootTeamUpdateBody,
    db: AsyncSession,
):
    try:
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

        m = await _map_tenant_team_by_local(db, tenant_id, team_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map Team cho UUID này",
            )

        payload = body.model_dump(exclude_unset=True, exclude_none=True)
        if not payload:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                "Cần ít nhất một trường để cập nhật",
            )

        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.application_request(
            "PATCH",
            f"/api/v1/accounts/{account_id}/teams/{m.chatwoot_id}",
            json_body=payload,
            params=pairs or None,
        )
        data = res.data
        if res.status_code == 200 and isinstance(data, dict):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã cập nhật Team trên Chatwoot",
                {
                    "tenant_id": str(tenant_id),
                    "team": _chatwoot_team_public(data, m.local_uuid),
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 503) else 502,
            "Cập nhật Team thất bại",
            _chatwoot_error_payload(res, sent_payload_keys=sorted(payload.keys(), key=str)),
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


async def delete_team(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    team_id: UUID,
    db: AsyncSession,
):
    try:
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

        m = await _map_tenant_team_by_local(db, tenant_id, team_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map Team cho UUID này",
            )

        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.application_request(
            "DELETE",
            f"/api/v1/accounts/{account_id}/teams/{m.chatwoot_id}",
            params=pairs or None,
        )
        if res.status_code in (200, 204):
            await db.delete(m)
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã xóa Team trên Chatwoot",
                {"tenant_id": str(tenant_id), "removed_team_id": str(team_id)},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 503) else 502,
            "Xóa Team thất bại",
            _chatwoot_error_payload(res),
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


async def list_team_members(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    team_id: UUID,
    db: AsyncSession,
):
    try:
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

        m = await _map_tenant_team_by_local(db, tenant_id, team_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map Team cho UUID này",
            )

        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.application_request(
            "GET",
            f"/api/v1/accounts/{account_id}/teams/{m.chatwoot_id}/team_members",
            params=pairs or None,
        )
        data = res.data
        if res.status_code == 200:
            raw_list = _agents_payload_as_list(data)
            if raw_list is None:
                return api_response(
                    ResponseStatus.SUCCESS,
                    ResponseStatusCode.OK,
                    "Danh sách thành viên team Chatwoot",
                    {"tenant_id": str(tenant_id), "team_id": str(team_id), "members": data},
                )
            public_members = []
            for item in raw_list:
                if not isinstance(item, dict) or item.get("id") is None:
                    public_members.append(item)
                    continue
                try:
                    cw_id = int(item["id"])
                except (TypeError, ValueError):
                    public_members.append(item)
                    continue
                am = await _ensure_tenant_agent_map(db, tenant_id, cw_id)
                public_members.append(_chatwoot_agent_public(item, am.local_uuid))
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Danh sách thành viên team Chatwoot",
                {"tenant_id": str(tenant_id), "team_id": str(team_id), "members": public_members},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 503) else 502,
            "Không lấy được danh sách thành viên team từ Chatwoot",
            _chatwoot_error_payload(res),
        )
    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi CSDL: {e}",
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi không xác định: {e}",
        )


async def _translate_local_agent_uuids_to_chatwoot(
    db: AsyncSession, tenant_id: UUID, local_uuids: list[UUID]
) -> tuple[list[int], list[str]]:
    if not local_uuids:
        return [], []
    q = await db.execute(
        select(ChatwootLegacyMap).where(
            and_(
                ChatwootLegacyMap.resource_type == ChatwootMapResourceType.AGENT,
                ChatwootLegacyMap.tenant_id == tenant_id,
                ChatwootLegacyMap.local_uuid.in_(local_uuids),
            )
        )
    )
    rows = q.scalars().all()
    found_map = {r.local_uuid: r.chatwoot_id for r in rows}
    found_ids = []
    missing_uuids = []
    for uid in local_uuids:
        if uid in found_map:
            found_ids.append(found_map[uid])
        else:
            missing_uuids.append(str(uid))
    return found_ids, missing_uuids


async def _modify_team_members(
    method: str,
    request: Request,
    current_user: User,
    tenant_id: UUID,
    team_id: UUID,
    body: ChatwootTeamMembersBody,
    db: AsyncSession,
    action_label: str,
):
    try:
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

        m = await _map_tenant_team_by_local(db, tenant_id, team_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map Team cho UUID này",
            )

        cw_agent_ids, missing_uuids = await _translate_local_agent_uuids_to_chatwoot(
            db, tenant_id, body.user_ids
        )
        if missing_uuids:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                f"Không tìm thấy map agent cho các UUID sau: {', '.join(missing_uuids)}",
            )

        payload = {"user_ids": cw_agent_ids}
        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.application_request(
            method,
            f"/api/v1/accounts/{account_id}/teams/{m.chatwoot_id}/team_members",
            json_body=payload,
            params=pairs or None,
        )
        data = res.data
        if res.status_code == 200:
            raw_list = _agents_payload_as_list(data)
            if raw_list is None:
                return api_response(
                    ResponseStatus.SUCCESS,
                    ResponseStatusCode.OK,
                    f"{action_label} thành công",
                    {"tenant_id": str(tenant_id), "team_id": str(team_id), "members": data},
                )
            public_members = []
            for item in raw_list:
                if not isinstance(item, dict) or item.get("id") is None:
                    public_members.append(item)
                    continue
                try:
                    cw_id = int(item["id"])
                except (TypeError, ValueError):
                    public_members.append(item)
                    continue
                am = await _ensure_tenant_agent_map(db, tenant_id, cw_id)
                public_members.append(_chatwoot_agent_public(item, am.local_uuid))
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                f"{action_label} thành công",
                {"tenant_id": str(tenant_id), "team_id": str(team_id), "members": public_members},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 422, 503) else 502,
            f"{action_label} thất bại",
            _chatwoot_error_payload(res, sent_payload_keys=["user_ids"]),
        )
    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi CSDL: {e}",
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi không xác định: {e}",
        )


async def add_team_members(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    team_id: UUID,
    body: ChatwootTeamMembersBody,
    db: AsyncSession,
):
    return await _modify_team_members(
        "POST", request, current_user, tenant_id, team_id, body, db, "Thêm thành viên vào team"
    )


async def remove_team_members(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    team_id: UUID,
    body: ChatwootTeamMembersBody,
    db: AsyncSession,
):
    return await _modify_team_members(
        "DELETE", request, current_user, tenant_id, team_id, body, db, "Xóa thành viên khỏi team"
    )


async def update_team_members(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    team_id: UUID,
    body: ChatwootTeamMembersBody,
    db: AsyncSession,
):
    return await _modify_team_members(
        "PATCH", request, current_user, tenant_id, team_id, body, db, "Cập nhật thành viên team"
    )
