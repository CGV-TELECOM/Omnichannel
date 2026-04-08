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
    Tenant,
    User,
    generate_uuid7,
)
from app.integrations.chatwoot import client as chatwoot_client
from app.integrations.chatwoot.account_payload import sanitize_platform_account_payload
from app.integrations.chatwoot.client import ChatwootResult
from app.schemas.requests.chatwoot import (
    ChatwootAgentBotCreateBody,
    ChatwootAgentBotUpdateBody,
    ChatwootAgentCreateBody,
    ChatwootAgentUpdateBody,
    ChatwootProvisionAccountBody,
    ChatwootUserCreateBody,
    ChatwootUserUpdateBody,
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
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                "Tenant không tồn tại",
            )

        exists = await _get_tenant_account_mapping(db, body.tenant_id)
        if exists:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.CONFLICT,
                "Tenant đã được map với Chatwoot account",
                {"chatwoot_account_id": exists.chatwoot_id},
            )

        payload = _platform_account_payload_provision(body)
        payload, sanitize_meta = sanitize_platform_account_payload(payload)
        res = await chatwoot_client.platform_request(
            "POST",
            "/platform/api/v1/accounts",
            json_body=payload,
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
        await db.commit()
        await db.refresh(row)

        success_data: dict[str, Any] = {
            "tenant_id": str(body.tenant_id),
            "chatwoot_account": data,
            "mapping_id": row.id,
            "integration_account_user": link_info,
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

        res = await chatwoot_client.platform_request(
            "GET",
            f"/platform/api/v1/accounts/{account_id}",
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

        res = await chatwoot_client.platform_request(
            "PATCH",
            f"/platform/api/v1/accounts/{account_id}",
            json_body=payload,
        )
        data = res.data
        if res.status_code == 200:
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


async def create_user(
    request: Request,
    current_user: User,
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

        local_q = await db.execute(select(User).where(User.id == body.local_user_id))
        local_user = local_q.scalar_one_or_none()
        if not local_user:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "User nội bộ không tồn tại",
            )

        exists = await _map_user_by_local(db, body.local_user_id)
        if exists:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.CONFLICT,
                "User đã có map Chatwoot",
                {"chatwoot_user_id": exists.chatwoot_id},
            )

        payload = body.model_dump(
            mode="json",
            exclude={"local_user_id"},
            exclude_none=True,
        )
        if "name" not in payload:
            payload["name"] = local_user.fullname or local_user.username
        if "display_name" not in payload and local_user.fullname:
            payload["display_name"] = local_user.fullname
        if "email" not in payload and local_user.email:
            payload["email"] = local_user.email

        if not payload.get("name") or not payload.get("email"):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                "Thiếu name/email để tạo user Chatwoot",
            )

        res = await chatwoot_client.platform_request(
            "POST",
            "/platform/api/v1/users",
            json_body=payload,
        )
        data = res.data
        if res.status_code in (200, 201) and isinstance(data, dict) and data.get("id") is not None:
            try:
                cw_id = int(data["id"])
            except (TypeError, ValueError):
                return api_response(
                    ResponseStatus.ERROR,
                    502,
                    "Chatwoot trả user không có id hợp lệ",
                    _chatwoot_error_payload(
                        res, sent_payload_keys=sorted(payload.keys(), key=str)
                    ),
                )
            m = await _ensure_user_map(db, body.local_user_id, cw_id)
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã tạo user trên Chatwoot",
                {"user": _chatwoot_user_public(data, m.local_uuid)},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 409, 422, 503) else 502,
            "Tạo user trên Chatwoot thất bại",
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
        m = await _map_user_by_local(db, user_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map user Chatwoot cho UUID này",
            )
        res = await chatwoot_client.platform_request(
            "GET",
            f"/platform/api/v1/users/{m.chatwoot_id}",
        )
        if res.status_code == 200 and isinstance(res.data, dict):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Lấy thông tin user Chatwoot thành công",
                {"user": _chatwoot_user_public(res.data, m.local_uuid)},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Không lấy được user từ Chatwoot",
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
        m = await _map_user_by_local(db, user_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map user Chatwoot cho UUID này",
            )
        payload = body.model_dump(mode="json", exclude_unset=True, exclude_none=True)
        if not payload:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                "Không có trường nào để cập nhật",
            )
        res = await chatwoot_client.platform_request(
            "PATCH",
            f"/platform/api/v1/users/{m.chatwoot_id}",
            json_body=payload,
        )
        if res.status_code == 200 and isinstance(res.data, dict):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã cập nhật user trên Chatwoot",
                {"user": _chatwoot_user_public(res.data, m.local_uuid)},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 422, 503) else 502,
            "Cập nhật user trên Chatwoot thất bại",
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
        m = await _map_user_by_local(db, user_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map user Chatwoot cho UUID này",
            )
        res = await chatwoot_client.platform_request(
            "DELETE",
            f"/platform/api/v1/users/{m.chatwoot_id}",
        )
        if res.status_code in (200, 204):
            await db.delete(m)
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã xóa user trên Chatwoot",
                {"removed_user_id": str(user_id)},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Xóa user trên Chatwoot thất bại",
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
                "Không có map user Chatwoot cho UUID này",
            )
        res = await chatwoot_client.platform_request(
            "GET",
            f"/platform/api/v1/users/{m.chatwoot_id}/login",
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
            "Không lấy được user SSO link từ Chatwoot",
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
        res = await chatwoot_client.platform_request(
            "DELETE",
            f"/platform/api/v1/accounts/{account_id}",
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


async def list_agents(
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

        res = await chatwoot_client.application_request(
            "GET",
            f"/api/v1/accounts/{account_id}/agents",
        )
        data = res.data
        if res.status_code == 200:
            raw_list = _agents_payload_as_list(data)
            if raw_list is None:
                return api_response(
                    ResponseStatus.SUCCESS,
                    ResponseStatusCode.OK,
                    "Danh sách agent Chatwoot",
                    {"tenant_id": str(tenant_id), "agents": data},
                )
            public: list[dict[str, Any]] = []
            for item in raw_list:
                if not isinstance(item, dict) or item.get("id") is None:
                    public.append(item)  # type: ignore[arg-type]
                    continue
                try:
                    cw_id = int(item["id"])
                except (TypeError, ValueError):
                    public.append(item)  # type: ignore[arg-type]
                    continue
                m = await _ensure_tenant_agent_map(db, tenant_id, cw_id)
                public.append(_chatwoot_agent_public(item, m.local_uuid))
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Danh sách agent Chatwoot",
                {"tenant_id": str(tenant_id), "agents": public},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 503) else 502,
            "Không lấy được danh sách agent từ Chatwoot",
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

        payload = _application_agent_payload(body)

        res = await chatwoot_client.application_request(
            "POST",
            f"/api/v1/accounts/{account_id}/agents",
            json_body=payload,
        )
        data = res.data
        if res.status_code in (200, 201) and isinstance(data, dict) and data.get("id") is not None:
            try:
                cw_id = int(data["id"])
            except (TypeError, ValueError):
                return api_response(
                    ResponseStatus.ERROR,
                    502,
                    "Chatwoot trả agent không có id hợp lệ",
                    _chatwoot_error_payload(res, sent_payload_keys=sorted(payload.keys(), key=str)),
                )
            m = await _ensure_tenant_agent_map(db, tenant_id, cw_id)
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã thêm agent trên Chatwoot",
                {
                    "tenant_id": str(tenant_id),
                    "agent": _chatwoot_agent_public(data, m.local_uuid),
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 503) else 502,
            "Thêm agent trên Chatwoot thất bại",
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

        m = await _map_tenant_agent_by_local(db, tenant_id, agent_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map agent cho UUID này (gọi GET agents để tạo map hoặc tạo agent mới)",
            )

        payload = _application_agent_payload(body)

        res = await chatwoot_client.application_request(
            "PATCH",
            f"/api/v1/accounts/{account_id}/agents/{m.chatwoot_id}",
            json_body=payload,
        )
        data = res.data
        if res.status_code == 200 and isinstance(data, dict):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã cập nhật agent trên Chatwoot",
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

        m = await _map_tenant_agent_by_local(db, tenant_id, agent_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map agent cho UUID này",
            )

        res = await chatwoot_client.application_request(
            "DELETE",
            f"/api/v1/accounts/{account_id}/agents/{m.chatwoot_id}",
        )
        if res.status_code in (200, 204):
            await db.delete(m)
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã xóa agent khỏi account Chatwoot",
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


async def list_all_agent_bots(
    request: Request,
    current_user: User,
    db: AsyncSession,
):
    """GET /platform/api/v1/agent_bots — toàn bộ bot trên instance Chatwoot (Platform API)."""
    try:
        if not await isCheckMaxLevel(current_user, db):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới thực hiện được thao tác này",
            )
        res = await chatwoot_client.platform_request(
            "GET", "/platform/api/v1/agent_bots"
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
                "Danh sách AgentBot Chatwoot (toàn instance, đã ẩn id Chatwoot)",
                {"agent_bots": redacted},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Không lấy được danh sách AgentBot từ Chatwoot",
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
    """Lọc bot thuộc đúng Chatwoot account đã map với tenant."""
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
        res = await chatwoot_client.platform_request(
            "GET", "/platform/api/v1/agent_bots"
        )
        if res.status_code != 200 or not isinstance(res.data, list):
            return api_response(
                ResponseStatus.ERROR,
                res.status_code if res.status_code in (401, 404, 503) else 502,
                "Không lấy được danh sách AgentBot từ Chatwoot",
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
                "Chưa có map Chatwoot account cho tenant này",
            )
        payload = _platform_agent_bot_create_payload(body, account_id)
        res = await chatwoot_client.platform_request(
            "POST",
            "/platform/api/v1/agent_bots",
            json_body=payload,
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
                    "Chatwoot trả AgentBot không có id hợp lệ",
                    _chatwoot_error_payload(
                        res, sent_payload_keys=sorted(payload.keys(), key=str)
                    ),
                )
            m = await _ensure_tenant_agent_bot_map(db, tenant_id, cw_id)
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã tạo AgentBot trên Chatwoot",
                {
                    "tenant_id": str(tenant_id),
                    "agent_bot": _chatwoot_agent_bot_public(res.data, m.local_uuid),
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Tạo AgentBot trên Chatwoot thất bại",
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
                "Chưa có map Chatwoot account cho tenant này",
            )
        m = await _map_tenant_agent_bot_by_local(db, tenant_id, bot_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map AgentBot cho UUID này",
            )
        res = await chatwoot_client.platform_request(
            "GET", f"/platform/api/v1/agent_bots/{m.chatwoot_id}"
        )
        if res.status_code != 200 or not isinstance(res.data, dict):
            return api_response(
                ResponseStatus.ERROR,
                res.status_code if res.status_code in (401, 404, 503) else 502,
                "Không lấy được AgentBot từ Chatwoot",
                _chatwoot_error_payload(res),
            )
        if not _agent_bot_belongs_to_account(res.data, account_id):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "AgentBot không thuộc account Chatwoot của tenant này",
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
                "Chưa có map Chatwoot account cho tenant này",
            )
        m = await _map_tenant_agent_bot_by_local(db, tenant_id, bot_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map AgentBot cho UUID này",
            )
        get_res = await chatwoot_client.platform_request(
            "GET", f"/platform/api/v1/agent_bots/{m.chatwoot_id}"
        )
        if get_res.status_code != 200 or not isinstance(get_res.data, dict):
            return api_response(
                ResponseStatus.ERROR,
                get_res.status_code
                if get_res.status_code in (401, 404, 503)
                else 502,
                "Không lấy được AgentBot từ Chatwoot",
                _chatwoot_error_payload(get_res),
            )
        if not _agent_bot_belongs_to_account(get_res.data, account_id):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "AgentBot không thuộc account Chatwoot của tenant này",
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
        )
        if res.status_code == 200 and isinstance(res.data, dict):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã cập nhật AgentBot trên Chatwoot",
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
                "Chưa có map Chatwoot account cho tenant này",
            )
        m = await _map_tenant_agent_bot_by_local(db, tenant_id, bot_id)
        if not m:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không có map AgentBot cho UUID này",
            )
        get_res = await chatwoot_client.platform_request(
            "GET", f"/platform/api/v1/agent_bots/{m.chatwoot_id}"
        )
        if get_res.status_code != 200 or not isinstance(get_res.data, dict):
            return api_response(
                ResponseStatus.ERROR,
                get_res.status_code
                if get_res.status_code in (401, 404, 503)
                else 502,
                "Không lấy được AgentBot từ Chatwoot",
                _chatwoot_error_payload(get_res),
            )
        if not _agent_bot_belongs_to_account(get_res.data, account_id):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "AgentBot không thuộc account Chatwoot của tenant này",
            )
        res = await chatwoot_client.platform_request(
            "DELETE", f"/platform/api/v1/agent_bots/{m.chatwoot_id}"
        )
        if res.status_code in (200, 204):
            await db.delete(m)
            await db.commit()
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã xóa AgentBot trên Chatwoot",
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
