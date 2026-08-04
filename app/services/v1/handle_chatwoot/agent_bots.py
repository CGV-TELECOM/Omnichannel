from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.integrations.chatwoot import client as chatwoot_client
from app.schemas.requests.chatwoot import (
    ChatwootAgentBotCreateBody,
    ChatwootAgentBotUpdateBody,
    ChatwootApplicationJsonBody,
)
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)
from app.utils.helpers import isCheckMaxLevel

from app.services.v1.handle_chatwoot._shared import (
    _forward_all_query_pairs,
    _agent_bot_belongs_to_account,
    _chatwoot_agent_bot_public,
    _chatwoot_error_payload,
    _ensure_tenant_agent_bot_map,
    _map_tenant_agent_bot_by_local,
    _platform_agent_bot_create_payload,
    _platform_agent_bot_update_payload,
    _resolve_account_id,
    _tenant_application_forward,
)


async def list_all_agent_bots(
    request: Request,
    current_user: User,
    db: AsyncSession,
):
    """GET /platform/api/v1/agent_bots — toàn bộ bot trên instance messaging (Platform API)."""
    try:
        if not await isCheckMaxLevel(current_user, db):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới thực hiện được thao tác này",
            )
        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.platform_request(
            "GET", "/platform/api/v1/agent_bots", params=pairs or None
        )

        if res.status_code == 200 and isinstance(res.data, list):
            redacted = [
                {
                    k: v
                    for k, v in b.items()
                    if k not in ("id", "account_id", "access_token")
                }
                if isinstance(b, dict)
                else b
                for b in res.data
            ]
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Danh sách AgentBot messaging (toàn instance, đã ẩn id messaging)",
                {"agent_bots": redacted},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Không lấy được danh sách AgentBot từ messaging",
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


async def list_tenant_agent_bots(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """Lọc bot thuộc đúng messaging account đã map với tenant."""
    try:
        if not await isCheckMaxLevel(current_user, db):
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
        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.platform_request(
            "GET", "/platform/api/v1/agent_bots", params=pairs or None
        )
        if res.status_code != 200 or not isinstance(res.data, list):
            return api_response(
                ResponseStatus.ERROR,
                res.status_code if res.status_code in (401, 404, 503) else 502,
                "Không lấy được danh sách AgentBot từ messaging",
                _chatwoot_error_payload(res),
            )
        filtered = [
            b
            for b in res.data
            if isinstance(b, dict) and _agent_bot_belongs_to_account(b, account_id)
        ]
        public_bots: list[dict[str, Any]] = []
        for b in filtered:
            if not isinstance(b, dict) or b.get("id") is None:
                continue
            try:
                cw_id = int(b["id"])
            except (TypeError, ValueError):
                continue
            m = await _ensure_tenant_agent_bot_map(db, tenant_id, cw_id)
            public_bots.append(_chatwoot_agent_bot_public(b, m.local_uuid))
        await db.commit()
        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Danh sách AgentBot của account tenant",
            {
                "tenant_id": str(tenant_id),
                "agent_bots": public_bots,
            },
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


async def create_agent_bot(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    body: ChatwootAgentBotCreateBody,
    db: AsyncSession,
):
    try:
        if not await isCheckMaxLevel(current_user, db):
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
        payload = _platform_agent_bot_create_payload(body, account_id)
        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.platform_request(
            "POST",
            "/platform/api/v1/agent_bots",
            json_body=payload,
            params=pairs or None,
        )
        if res.status_code in (200, 201) and isinstance(res.data, dict) and res.data.get(
            "id"
        ) is not None:
            try:
                cw_id = int(res.data["id"])
            except (TypeError, ValueError):
                return api_response(
                    ResponseStatus.ERROR,
                    502,
                    "Messaging trả AgentBot không có id hợp lệ",
                    _chatwoot_error_payload(
                        res, sent_payload_keys=sorted(payload.keys(), key=str)
                    ),
                )
            m = await _ensure_tenant_agent_bot_map(db, tenant_id, cw_id)
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã tạo AgentBot trên messaging",
                {
                    "tenant_id": str(tenant_id),
                    "agent_bot": _chatwoot_agent_bot_public(res.data, m.local_uuid),
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Tạo AgentBot trên messaging thất bại",
            _chatwoot_error_payload(
                res, sent_payload_keys=sorted(payload.keys(), key=str)
            ),
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


async def get_agent_bot(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    bot_id: UUID,
    db: AsyncSession,
):
    try:
        if not await isCheckMaxLevel(current_user, db):
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
        m = await _map_tenant_agent_bot_by_local(db, tenant_id, bot_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map AgentBot cho UUID này",
            )
        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.platform_request(
            "GET", f"/platform/api/v1/agent_bots/{m.chatwoot_id}", params=pairs or None
        )
        if res.status_code != 200 or not isinstance(res.data, dict):
            return api_response(
                ResponseStatus.ERROR,
                res.status_code if res.status_code in (401, 404, 503) else 502,
                "Không lấy được AgentBot từ messaging",
                _chatwoot_error_payload(res),
            )
        if not _agent_bot_belongs_to_account(res.data, account_id):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "AgentBot không thuộc account messaging của tenant này",
            )
        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Chi tiết AgentBot",
            {
                "tenant_id": str(tenant_id),
                "agent_bot": _chatwoot_agent_bot_public(res.data, m.local_uuid),
            },
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


async def update_agent_bot(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    bot_id: UUID,
    body: ChatwootAgentBotUpdateBody,
    db: AsyncSession,
):
    try:
        if not await isCheckMaxLevel(current_user, db):
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
        m = await _map_tenant_agent_bot_by_local(db, tenant_id, bot_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map AgentBot cho UUID này",
            )
        pairs = _forward_all_query_pairs(request)
        get_res = await chatwoot_client.platform_request(
            "GET", f"/platform/api/v1/agent_bots/{m.chatwoot_id}", params=pairs or None
        )
        if get_res.status_code != 200 or not isinstance(get_res.data, dict):
            return api_response(
                ResponseStatus.ERROR,
                get_res.status_code
                if get_res.status_code in (401, 404, 503)
                else 502,
                "Không lấy được AgentBot từ messaging",
                _chatwoot_error_payload(get_res),
            )
        if not _agent_bot_belongs_to_account(get_res.data, account_id):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "AgentBot không thuộc account messaging của tenant này",
            )
        payload = _platform_agent_bot_update_payload(body)
        if not payload:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                "Cần ít nhất một trường để cập nhật",
            )
        res = await chatwoot_client.platform_request(
            "PATCH",
            f"/platform/api/v1/agent_bots/{m.chatwoot_id}",
            json_body=payload,
            params=pairs or None,
        )
        if res.status_code == 200 and isinstance(res.data, dict):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã cập nhật AgentBot trên messaging",
                {
                    "tenant_id": str(tenant_id),
                    "agent_bot": _chatwoot_agent_bot_public(res.data, m.local_uuid),
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Cập nhật AgentBot thất bại",
            _chatwoot_error_payload(
                res, sent_payload_keys=sorted(payload.keys(), key=str)
            ),
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


async def delete_agent_bot(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    bot_id: UUID,
    db: AsyncSession,
):
    try:
        if not await isCheckMaxLevel(current_user, db):
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
        m = await _map_tenant_agent_bot_by_local(db, tenant_id, bot_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map AgentBot cho UUID này",
            )
        pairs = _forward_all_query_pairs(request)
        get_res = await chatwoot_client.platform_request(
            "GET", f"/platform/api/v1/agent_bots/{m.chatwoot_id}", params=pairs or None
        )
        if get_res.status_code != 200 or not isinstance(get_res.data, dict):
            return api_response(
                ResponseStatus.ERROR,
                get_res.status_code
                if get_res.status_code in (401, 404, 503)
                else 502,
                "Không lấy được AgentBot từ messaging",
                _chatwoot_error_payload(get_res),
            )
        if not _agent_bot_belongs_to_account(get_res.data, account_id):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "AgentBot không thuộc account messaging của tenant này",
            )
        res = await chatwoot_client.platform_request(
            "DELETE", f"/platform/api/v1/agent_bots/{m.chatwoot_id}", params=pairs or None
        )
        if res.status_code in (200, 204):
            await db.delete(m)
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã xóa AgentBot trên messaging",
                {
                    "tenant_id": str(tenant_id),
                    "removed_agent_bot_id": str(bot_id),
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Xóa AgentBot thất bại",
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


async def list_account_agent_bots(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """GET /api/v1/accounts/{account_id}/agent_bots."""
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="GET",
        path_suffix="/agent_bots",
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Danh sách Account AgentBots",
        error_message="Không lấy được danh sách Account AgentBots từ messaging",
    )


async def create_account_agent_bot(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    body: ChatwootApplicationJsonBody,
    db: AsyncSession,
):
    """POST /api/v1/accounts/{account_id}/agent_bots."""
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="POST",
        path_suffix="/agent_bots",
        json_body=payload,
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Đã tạo Account AgentBot",
        success_codes=frozenset({200, 201}),
        error_message="Tạo Account AgentBot thất bại",
        error_payload_keys=sorted(payload.keys(), key=str),
    )


async def get_account_agent_bot(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    agent_bot_id: int,
    db: AsyncSession,
):
    """GET /api/v1/accounts/{account_id}/agent_bots/{agent_bot_id}."""
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="GET",
        path_suffix=f"/agent_bots/{agent_bot_id}",
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Chi tiết Account AgentBot",
        extra_response={"agent_bot_id": agent_bot_id},
        error_message="Không lấy được Account AgentBot từ messaging",
    )


async def update_account_agent_bot(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    agent_bot_id: int,
    body: ChatwootApplicationJsonBody,
    db: AsyncSession,
):
    """PATCH /api/v1/accounts/{account_id}/agent_bots/{agent_bot_id}."""
    payload = body.model_dump(mode="json", exclude_unset=True, exclude_none=True)
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="PATCH",
        path_suffix=f"/agent_bots/{agent_bot_id}",
        json_body=payload,
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Đã cập nhật Account AgentBot",
        extra_response={"agent_bot_id": agent_bot_id},
        error_message="Cập nhật Account AgentBot thất bại",
        error_payload_keys=sorted(payload.keys(), key=str),
    )


async def delete_account_agent_bot(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    agent_bot_id: int,
    db: AsyncSession,
):
    """DELETE /api/v1/accounts/{account_id}/agent_bots/{agent_bot_id}."""
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="DELETE",
        path_suffix=f"/agent_bots/{agent_bot_id}",
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Đã xóa Account AgentBot",
        success_codes=frozenset({200, 204}),
        extra_response={"agent_bot_id": agent_bot_id},
        error_message="Xóa Account AgentBot thất bại",
    )

