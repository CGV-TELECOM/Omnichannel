from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatwootLegacyMap, ChatwootMapResourceType, Tenant, User
from app.integrations.chatwoot import client as chatwoot_client
from app.integrations.chatwoot.account_payload import sanitize_platform_account_payload
from app.schemas.requests.chatwoot import ChatwootProvisionAccountBody, ChatwootUpdateAccountBody
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)
from app.utils.helpers import isCheckMaxLevel

from app.services.v1.handle_chatwoot._shared import (
    _INTEGRATION_ACCOUNT_USER_ROLE,
    _chatwoot_error_payload,
    _delete_tenant_agent_and_bot_maps,
    _forward_all_query_pairs,
    _get_tenant_account_mapping,
    _platform_account_payload_provision,
    _platform_account_payload_update,
    _resolve_account_id,
    link_integration_user_to_chatwoot_account,
)


async def provision_account(
    request: Request,
    current_user: User,
    body: ChatwootProvisionAccountBody,
    db: AsyncSession,
):
    try:
        if not await isCheckMaxLevel(current_user, db):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới thực hiện được thao tác này",
            )

        tenant_q = await db.execute(select(Tenant).where(Tenant.id == body.tenant_id))
        tenant = tenant_q.scalar_one_or_none()
        if not tenant:
            # Đồng bộ ngược: tạo tenant nội bộ trước khi provision Chatwoot account.
            tenant = Tenant(
                id=body.tenant_id,
                name=body.name,
                description=getattr(body, "description", None),
                is_active=1,
            )
            db.add(tenant)
            await db.flush()

        exists = await _get_tenant_account_mapping(db, body.tenant_id)
        if exists:
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Tenant đã được liên kết với Chatwoot account (bỏ qua tạo mới)",
                {"tenant_id": str(body.tenant_id), "chatwoot_linked": True},
            )

        payload = _platform_account_payload_provision(body)
        payload, sanitize_meta = sanitize_platform_account_payload(payload)
        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.platform_request(
            "POST",
            "/platform/api/v1/accounts",
            json_body=payload,
            params=pairs or None,
        )

        data = res.data
        status = res.status_code
        if status not in (200, 201) or not isinstance(data, dict) or data.get("id") is None:
            err_code = status if status in (401, 404, 503) else 502
            hint = (
                " Gợi ý: locale (en, vi…); domain hợp lệ hoặc không gửi. "
                "Key trong `features` phải là flag hợp lệ (unknown đã bị bỏ trước khi gửi). "
                "Xem `raw_response_body_preview` và log Rails trên server Chatwoot."
            )
            if isinstance(data, dict) and int(data.get("status", 0) or 0) >= 500:
                msg = "Chatwoot server báo lỗi nội bộ." + hint
            else:
                msg = "Chatwoot tạo account thất bại." + hint
            detail = _chatwoot_error_payload(
                res,
                sent_payload_keys=sorted(payload.keys(), key=str),
            )
            if sanitize_meta:
                detail["payload_sanitize_meta"] = sanitize_meta
            return api_response(ResponseStatus.ERROR, err_code, msg, detail)

        chat_id = int(data["id"])
        link_info = await link_integration_user_to_chatwoot_account(chat_id)

        row = ChatwootLegacyMap(
            resource_type=ChatwootMapResourceType.ACCOUNT,
            local_uuid=body.tenant_id,
            chatwoot_id=chat_id,
            tenant_id=body.tenant_id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        if not isinstance(tenant.meta_data, dict):
            tenant.meta_data = {}
        prev = tenant.meta_data.get("chatwoot_account") if isinstance(tenant.meta_data, dict) else None
        base = dict(prev) if isinstance(prev, dict) else {}
        tenant.meta_data["chatwoot_account"] = {**base, **dict(payload)}
        await db.commit()
        await db.refresh(row)

        success_data: dict[str, Any] = {
            "tenant_id": str(body.tenant_id),
            "chatwoot_linked": True,
        }
        if sanitize_meta:
            success_data["payload_sanitize_meta"] = sanitize_meta
        msg = "Đã tạo Chatwoot account và lưu map tenant"
        if link_info.get("linked"):
            msg += (
                ". Đã gắn user tích hợp (Application API) vào account với role "
                f"{_INTEGRATION_ACCOUNT_USER_ROLE}."
            )
        elif link_info.get("attempted") and not link_info.get("linked"):
            msg += (
                ". Cảnh báo: không gắn được user tích hợp vào account — "
                "xem integration_account_user trong data."
            )
        else:
            sr = link_info.get("skipped_reason") or ""
            msg += (
                ". Gợi ý: cấu hình CHATWOOT_USER_API_TOKEN hoặc CHATWOOT_INTEGRATION_USER_ID "
                f"để tự gắn user vào account khi provision. ({sr})"
            )
        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            msg,
            success_data,
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


async def get_account(
    request: Request,
    current_user: User,
    tenant_id: UUID,
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
                "Chưa có map Chatwoot account cho tenant này",
            )

        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.platform_request(
            "GET",
            f"/platform/api/v1/accounts/{account_id}",
            params=pairs or None,
        )
        data = res.data
        if res.status_code == 200:
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Lấy thông tin Chatwoot account thành công",
                {"tenant_id": str(tenant_id), "chatwoot_account": data},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Không lấy được account từ Chatwoot",
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


async def sync_integration_account_user(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """Gắn lại user tích hợp vào account đã map (tenant cũ hoặc sau khi sửa .env)."""
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
                "Chưa có map Chatwoot account cho tenant này",
            )

        link_info = await link_integration_user_to_chatwoot_account(account_id)
        payload = {
            "tenant_id": str(tenant_id),
            "chatwoot_account_id": account_id,
            "integration_account_user": link_info,
        }
        if link_info.get("linked"):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã gắn user tích hợp vào account Chatwoot",
                payload,
            )
        if not link_info.get("attempted"):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                link_info.get("skipped_reason") or "Không thể xác định user tích hợp",
                payload,
            )
        return api_response(
            ResponseStatus.ERROR,
            502,
            "Chatwoot từ chối gắn user tích hợp vào account",
            payload,
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


async def update_account(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    body: ChatwootUpdateAccountBody,
    db: AsyncSession,
):
    try:
        if not await isCheckMaxLevel(current_user, db):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới thực hiện được thao tác này",
            )

        payload = _platform_account_payload_update(body)
        payload, sanitize_meta = sanitize_platform_account_payload(payload)
        if not payload:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                "Không có trường nào để cập nhật",
                {"payload_sanitize_meta": sanitize_meta} if sanitize_meta else None,
            )

        account_id, _ = await _resolve_account_id(db, tenant_id)
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Chưa có map Chatwoot account cho tenant này",
            )

        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.platform_request(
            "PATCH",
            f"/platform/api/v1/accounts/{account_id}",
            json_body=payload,
            params=pairs or None,
        )
        data = res.data
        if res.status_code == 200:
            tenant_q = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
            tenant = tenant_q.scalar_one_or_none()
            if tenant:
                if not isinstance(tenant.meta_data, dict):
                    tenant.meta_data = {}
                current_meta = tenant.meta_data.get("chatwoot_account")
                if not isinstance(current_meta, dict):
                    current_meta = {}
                tenant.meta_data["chatwoot_account"] = {**current_meta, **dict(payload)}
                await db.commit()
            ok_data: dict[str, Any] = {
                "tenant_id": str(tenant_id),
                "chatwoot_account": data,
            }
            if sanitize_meta:
                ok_data["payload_sanitize_meta"] = sanitize_meta
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Cập nhật Chatwoot account thành công",
                ok_data,
            )
        err_detail = _chatwoot_error_payload(
            res, sent_payload_keys=sorted(payload.keys(), key=str)
        )
        if sanitize_meta:
            err_detail["payload_sanitize_meta"] = sanitize_meta
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Cập nhật Chatwoot account thất bại",
            err_detail,
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

async def delete_account(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    try:
        if not await isCheckMaxLevel(current_user, db):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới thực hiện được thao tác này",
            )

        mapping = await _get_tenant_account_mapping(db, tenant_id)
        if not mapping:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Chưa có map Chatwoot account cho tenant này",
            )

        account_id = mapping.chatwoot_id
        pairs = _forward_all_query_pairs(request)
        res = await chatwoot_client.platform_request(
            "DELETE",
            f"/platform/api/v1/accounts/{account_id}",
            params=pairs or None,
        )
        if res.status_code not in (200, 204):
            return api_response(
                ResponseStatus.ERROR,
                res.status_code if res.status_code in (401, 404, 503) else 502,
                "Xóa account trên Chatwoot thất bại",
                _chatwoot_error_payload(res),
            )

        await _delete_tenant_agent_and_bot_maps(db, tenant_id)
        await db.delete(mapping)
        await db.commit()
        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Đã xóa Chatwoot account và bản ghi map",
            {"tenant_id": str(tenant_id), "removed_chatwoot_account_id": account_id},
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

