from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.integrations.chatwoot import client as chatwoot_client
from app.schemas.requests.chatwoot import ChatwootAgentCreateBody, ChatwootAgentUpdateBody
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)
from app.utils.helpers import is_platform_admin

from app.services.v1.handle_chatwoot._shared import (
    _forward_all_query_pairs,
    _agents_payload_as_list,
    _application_agent_payload,
    _chatwoot_agent_public,
    _chatwoot_error_payload,
    _ensure_tenant_agent_map,
    _ensure_tenant_agent_maps_bulk,
    _map_tenant_agent_by_local,
    _resolve_account_id,
)


async def list_agents(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    try:
        # if not await is_platform_admin(current_user, db):
        #     return api_response(
        #         ResponseStatus.ERROR,
        #         ResponseStatusCode.FORBIDDEN,
        #         "Chỉ quản trị viên mới thực hiện được thao tác này",
        #     )

        account_id, _ = await _resolve_account_id(db, tenant_id)
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Chưa có map messaging account cho tenant này",
            )

        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.application_request(
            "GET",
            f"/api/v1/accounts/{account_id}/agents",
            params=pairs or None,
        )
        data = res.data
        if res.status_code == 200:
            raw_list = _agents_payload_as_list(data)
            if raw_list is None:
                return api_response(
                    ResponseStatus.SUCCESS,
                    ResponseStatusCode.OK,
                    "Danh sách agent messaging",
                    {"tenant_id": str(tenant_id), "agents": data},
                )
            public: list[dict[str, Any]] = []
            cw_ids: list[int] = []
            parsed: list[tuple[dict[str, Any], int | None]] = []
            for item in raw_list:
                if not isinstance(item, dict) or item.get("id") is None:
                    parsed.append((item, None))  # type: ignore[arg-type]
                    continue
                try:
                    cw_id = int(item["id"])
                except (TypeError, ValueError):
                    parsed.append((item, None))
                    continue
                cw_ids.append(cw_id)
                parsed.append((item, cw_id))

            maps = await _ensure_tenant_agent_maps_bulk(db, tenant_id, cw_ids)
            for item, cw_id in parsed:
                if cw_id is None:
                    public.append(item)
                    continue
                public.append(_chatwoot_agent_public(item, maps[cw_id].local_uuid))
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Danh sách agent messaging",
                {"tenant_id": str(tenant_id), "agents": public},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 503) else 502,
            "Không lấy được danh sách agent từ messaging",
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


async def create_agent(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    body: ChatwootAgentCreateBody,
    db: AsyncSession,
):
    try:
        if not await is_platform_admin(current_user, db):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới thực hiện được thao tác này",
            )

        account_id, _ = await _resolve_account_id(db, tenant_id)
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Chưa có map messaging account cho tenant này",
            )

        payload = _application_agent_payload(body)

        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.application_request(
            "POST",
            f"/api/v1/accounts/{account_id}/agents",
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
                    "Messaging trả agent không có id hợp lệ",
                    _chatwoot_error_payload(res, sent_payload_keys=sorted(payload.keys(), key=str)),
                )
            m = await _ensure_tenant_agent_map(db, tenant_id, cw_id)
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã thêm agent trên messaging",
                {
                    "tenant_id": str(tenant_id),
                    "agent": _chatwoot_agent_public(data, m.local_uuid),
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 503) else 502,
            "Thêm agent trên messaging thất bại",
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


async def update_agent(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    agent_id: UUID,
    body: ChatwootAgentUpdateBody,
    db: AsyncSession,
):
    try:
        # if not await is_platform_admin(current_user, db):
        #     return api_response(
        #         ResponseStatus.ERROR,
        #         ResponseStatusCode.FORBIDDEN,
        #         "Chỉ quản trị viên mới thực hiện được thao tác này",
        #     )
        account_id, _ = await _resolve_account_id(db, tenant_id)
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Chưa có map messaging account cho tenant này",
            )

        m = await _map_tenant_agent_by_local(db, tenant_id, agent_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map agent cho UUID này (gọi GET agents để tạo map hoặc tạo agent mới)",
            )

        payload = _application_agent_payload(body)

        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.application_request(
            "PATCH",
            f"/api/v1/accounts/{account_id}/agents/{m.chatwoot_id}",
            json_body=payload,
            params=pairs or None,
        )
        data = res.data
        if res.status_code == 200 and isinstance(data, dict):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã cập nhật agent trên messaging",
                {
                    "tenant_id": str(tenant_id),
                    "agent": _chatwoot_agent_public(data, m.local_uuid),
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 503) else 502,
            "Cập nhật agent thất bại",
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


async def delete_agent(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    agent_id: UUID,
    db: AsyncSession,
):
    try:
        if not await is_platform_admin(current_user, db):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới thực hiện được thao tác này",
            )

        account_id, _ = await _resolve_account_id(db, tenant_id)
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Chưa có map messaging account cho tenant này",
            )

        m = await _map_tenant_agent_by_local(db, tenant_id, agent_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map agent cho UUID này",
            )

        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.application_request(
            "DELETE",
            f"/api/v1/accounts/{account_id}/agents/{m.chatwoot_id}",
            params=pairs or None,
        )
        if res.status_code in (200, 204):
            await db.delete(m)
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã xóa agent khỏi account messaging",
                {"tenant_id": str(tenant_id), "removed_agent_id": str(agent_id)},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 503) else 502,
            "Xóa agent thất bại",
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

