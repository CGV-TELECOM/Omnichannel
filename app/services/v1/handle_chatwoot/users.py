from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.integrations.chatwoot import client as chatwoot_client
from app.schemas.requests.chatwoot import ChatwootUserCreateBody, ChatwootUserUpdateBody
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)
from app.utils.helpers import isCheckMaxLevel

from app.services.v1.handle_chatwoot._shared import (
    _chatwoot_error_payload,
    _chatwoot_user_public,
    _ensure_user_map,
    _forward_all_query_pairs,
    _map_user_by_local,
    _merge_chatwoot_platform_user_payload,
    _resolve_account_id,
)


def _normalize_user_payload_for_agent(
    payload: dict[str, Any],
    *,
    local_user: User | None,
    for_update: bool,
) -> dict[str, Any]:
    out = dict(payload)

    # Accept client aliases from internal user forms.
    if out.get("name") is None and out.get("fullname") is not None:
        out["name"] = out.get("fullname")
    if out.get("name") is None and out.get("full_name") is not None:
        out["name"] = out.get("full_name")
    if out.get("display_name") is None and out.get("name") is not None:
        out["display_name"] = out.get("name")

    # Fill required fields for create flow.
    if not for_update:
        if out.get("name") is None and local_user is not None:
            out["name"] = local_user.fullname or local_user.username
        if out.get("display_name") is None and local_user is not None:
            out["display_name"] = (
                out.get("name") or local_user.fullname or local_user.username
            )
        if out.get("email") is None and local_user is not None:
            out["email"] = local_user.email

    # Chatwoot agent API requires role on create/update.
    out.setdefault("role", "agent")

    # Do not forward internal aliases to Chatwoot.
    out.pop("fullname", None)
    out.pop("full_name", None)
    return {k: v for k, v in out.items() if v is not None}


async def _require_user_and_account(
    db: AsyncSession, user_id: UUID
) -> tuple[User | None, int | None, Any]:
    local_q = await db.execute(select(User).where(User.id == user_id))
    local_user = local_q.scalar_one_or_none()
    if not local_user or local_user.tenant_id is None:
        return local_user, None, None
    account_id, account_map = await _resolve_account_id(
        db, UUID(str(local_user.tenant_id))
    )
    return local_user, account_id, account_map


async def create_user(
    request: Request,
    current_user: User,
    user_id: UUID,
    body: ChatwootUserCreateBody,
    db: AsyncSession,
):
    try:
        if not await isCheckMaxLevel(current_user, db):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới thực hiện được thao tác này",
            )

        local_user, account_id, _ = await _require_user_and_account(db, user_id)
        if not local_user:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "User nội bộ không tồn tại",
            )
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Tenant của user chưa có map messaging account",
            )

        exists = await _map_user_by_local(db, user_id)
        if exists:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.CONFLICT,
                "User đã có map messaging",
                {"messaging_user_id": exists.chatwoot_id},
            )

        core = _normalize_user_payload_for_agent(
            dict(body.model_dump(mode="json", exclude_none=True)),
            local_user=local_user,
            for_update=False,
        )

        merged = _merge_chatwoot_platform_user_payload(
            local_user.meta_data if isinstance(local_user.meta_data, dict) else None,
            core,
        )

        if not merged.get("name") or not merged.get("email"):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                "Thiếu name/email để tạo user messaging",
            )

        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.application_request(
            "POST",
            f"/api/v1/accounts/{account_id}/agents",
            json_body=merged,
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
                    "Messaging trả user không có id hợp lệ",
                    _chatwoot_error_payload(
                        res, sent_payload_keys=sorted(merged.keys(), key=str)
                    ),
                )
            m = await _ensure_user_map(db, user_id, cw_id)
            if not isinstance(local_user.meta_data, dict):
                local_user.meta_data = {}
            else:
                local_user.meta_data = dict(local_user.meta_data)
            local_user.meta_data["chatwoot_user"] = {
                k: v for k, v in merged.items() if k != "password"
            }
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã tạo user trên messaging",
                {"user": _chatwoot_user_public(data, m.local_uuid)},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 409, 422, 503) else 502,
            "Tạo user trên messaging thất bại",
            _chatwoot_error_payload(res, sent_payload_keys=sorted(merged.keys(), key=str)),
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


async def get_user(
    request: Request,
    current_user: User,
    user_id: UUID,
    db: AsyncSession,
):
    try:
        if not await isCheckMaxLevel(current_user, db):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới thực hiện được thao tác này",
            )
        local_user, account_id, _ = await _require_user_and_account(db, user_id)
        if not local_user:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "User nội bộ không tồn tại",
            )
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Tenant của user chưa có map messaging account",
            )
        m = await _map_user_by_local(db, user_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map user messaging cho UUID này",
            )
        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.application_request(
            "GET",
            f"/api/v1/accounts/{account_id}/agents/{m.chatwoot_id}",
            params=pairs or None,
        )
        if res.status_code == 200 and isinstance(res.data, dict):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Lấy thông tin user messaging thành công",
                {"user": _chatwoot_user_public(res.data, m.local_uuid)},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Không lấy được user từ messaging",
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


async def update_user(
    request: Request,
    current_user: User,
    user_id: UUID,
    body: ChatwootUserUpdateBody,
    db: AsyncSession,
):
    try:
        if not await isCheckMaxLevel(current_user, db):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới thực hiện được thao tác này",
            )
        local_user, account_id, _ = await _require_user_and_account(db, user_id)
        if not local_user:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "User nội bộ không tồn tại",
            )
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Tenant của user chưa có map messaging account",
            )
        m = await _map_user_by_local(db, user_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map user messaging cho UUID này",
            )
        payload = _normalize_user_payload_for_agent(
            dict(body.model_dump(mode="json", exclude_unset=True, exclude_none=True)),
            local_user=local_user,
            for_update=True,
        )
        if not payload:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                "Không có trường nào để cập nhật",
            )
        merged = _merge_chatwoot_platform_user_payload(
            local_user.meta_data if local_user and isinstance(local_user.meta_data, dict) else None,
            payload,
        )
        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.application_request(
            "PATCH",
            f"/api/v1/accounts/{account_id}/agents/{m.chatwoot_id}",
            json_body=merged,
            params=pairs or None,
        )
        if res.status_code == 200 and isinstance(res.data, dict):
            if local_user:
                if not isinstance(local_user.meta_data, dict):
                    local_user.meta_data = {}
                else:
                    local_user.meta_data = dict(local_user.meta_data)
                cur = local_user.meta_data.get("chatwoot_user")
                cur_d = dict(cur) if isinstance(cur, dict) else {}
                snap = {**cur_d, **{k: v for k, v in merged.items() if k != "password"}}
                local_user.meta_data["chatwoot_user"] = snap
                await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã cập nhật user trên messaging",
                {"user": _chatwoot_user_public(res.data, m.local_uuid)},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 422, 503) else 502,
            "Cập nhật user trên messaging thất bại",
            _chatwoot_error_payload(res, sent_payload_keys=sorted(merged.keys(), key=str)),
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


async def delete_user(
    request: Request,
    current_user: User,
    user_id: UUID,
    db: AsyncSession,
):
    try:
        if not await isCheckMaxLevel(current_user, db):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới thực hiện được thao tác này",
            )
        local_user, account_id, _ = await _require_user_and_account(db, user_id)
        if not local_user:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "User nội bộ không tồn tại",
            )
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Tenant của user chưa có map messaging account",
            )
        m = await _map_user_by_local(db, user_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map user messaging cho UUID này",
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
                "Đã xóa user trên messaging",
                {"removed_user_id": str(user_id)},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Xóa user trên messaging thất bại",
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


async def get_user_sso_link(
    request: Request,
    current_user: User,
    user_id: UUID,
    db: AsyncSession,
):
    try:
        if not await isCheckMaxLevel(current_user, db):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới thực hiện được thao tác này",
            )
        m = await _map_user_by_local(db, user_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map user messaging cho UUID này",
            )
        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.platform_request(
            "GET",
            f"/platform/api/v1/users/{m.chatwoot_id}/login",
            params=pairs or None,
        )
        if res.status_code == 200 and isinstance(res.data, dict):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Lấy user SSO link thành công",
                {"user_id": str(user_id), "sso": res.data},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 422, 503) else 502,
            "Không lấy được user SSO link từ messaging",
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
