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
from app.schemas.requests.chatwoot import ChatwootProvisionAccountBody, ChatwootUpdateAccountBody, ChatwootBulkActionLabelsBody, ChatwootCustomFiltersBody, ChatwootActionAgentInboxesBody
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)
from app.utils.helpers import is_platform_admin

from app.services.v1.handle_chatwoot._shared import (
    _INTEGRATION_ACCOUNT_USER_ROLE,
    _chatwoot_error_payload,
    _delete_tenant_agent_and_bot_maps,
    _forward_all_query_pairs,
    _get_tenant_account_mapping,
    _map_tenant_agent_by_local,
    _map_tenant_team_by_local,
    _platform_account_payload_provision,
    _platform_account_payload_update,
    _resolve_account_id,
    _translate_local_agent_uuids_to_remote,
    link_integration_user_to_chatwoot_account,
)


async def provision_account(
    request: Request,
    current_user: User,
    body: ChatwootProvisionAccountBody,
    db: AsyncSession,
):
    try:
        if not await is_platform_admin(current_user, db):
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
                "Tenant đã được liên kết với messaging account (bỏ qua tạo mới)",
                {"tenant_id": str(body.tenant_id), "messaging_linked": True},
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
                "Xem `raw_response_body_preview` và log Rails trên server messaging."
            )
            if isinstance(data, dict) and int(data.get("status", 0) or 0) >= 500:
                msg = "Messaging server báo lỗi nội bộ." + hint
            else:
                msg = "Messaging tạo account thất bại." + hint
            detail = _chatwoot_error_payload(
                res,
                sent_payload_keys=sorted(payload.keys(), key=str),
            )
            if sanitize_meta:
                detail["payload_sanitize_meta"] = sanitize_meta
            return api_response(ResponseStatus.ERROR, err_code, msg, detail)

        chat_id = int(data["id"])
        link_info = await link_integration_user_to_chatwoot_account(chat_id)
        if not link_info.get("linked"):
            import logging
            logger = logging.getLogger(__name__)
            try:
                await chatwoot_client.platform_request(
                    "DELETE",
                    f"/platform/api/v1/accounts/{chat_id}",
                )
            except Exception as delete_ex:
                logger.error(
                    "Lỗi khi xóa account messaging %s sau khi liên kết thất bại: %s",
                    chat_id,
                    str(delete_ex),
                )
            await db.rollback()
            msg = "Gắn user tích hợp vào messaging account thất bại, đã rollback tạo doanh nghiệp"
            if link_info.get("skipped_reason"):
                msg += f". Lý do: {link_info.get('skipped_reason')}"
            return api_response(
                ResponseStatus.ERROR,
                502,
                msg,
                {"integration_account_user": link_info},
            )


        # Tự động đăng ký webhook của Backend lên Chatwoot nếu cấu hình PUBLIC_BACKEND_URL
        from app.core.config.app_config import settings
        import logging
        logger = logging.getLogger(__name__)
        if settings.PUBLIC_BACKEND_URL:
            webhook_url = f"{settings.PUBLIC_BACKEND_URL.rstrip('/')}/api/v1/chatwoot-webhooks"
            try:
                webhook_res = await chatwoot_client.application_request(
                    "POST",
                    f"/api/v1/accounts/{chat_id}/webhooks",
                    json_body={
                        "url": webhook_url,
                        "subscriptions": [
                            "message_created",
                            "message_updated",
                            "conversation_created",
                            "conversation_updated",
                            "conversation_status_changed",
                        ],
                    },
                )
                if webhook_res.status_code in (200, 201):
                    logger.info(
                        "Đã đăng ký webhook messaging tự động thành công cho account %s: %s",
                        chat_id,
                        webhook_url,
                    )
                else:
                    logger.warning(
                        "Đăng ký webhook messaging tự động thất bại cho account %s. Status: %s. Response: %s",
                        chat_id,
                        webhook_res.status_code,
                        webhook_res.raw_text,
                    )
            except Exception as e:
                logger.error(
                    "Lỗi khi tự động đăng ký webhook messaging cho account %s: %s",
                    chat_id,
                    str(e),
                )

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
            "messaging_linked": True,
        }
        if sanitize_meta:
            success_data["payload_sanitize_meta"] = sanitize_meta
        msg = (
            "Đã tạo messaging account và lưu map tenant. "
            "Đã gắn user tích hợp (Application API) vào account với role "
            f"{_INTEGRATION_ACCOUNT_USER_ROLE}."
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
                "Lấy thông tin messaging account thành công",
                {"tenant_id": str(tenant_id), "messaging_account": data},
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Không lấy được account từ messaging",
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

        link_info = await link_integration_user_to_chatwoot_account(account_id)
        payload = {
            "tenant_id": str(tenant_id),
            "messaging_account_id": account_id,
            "integration_account_user": link_info,
        }
        if link_info.get("linked"):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã gắn user tích hợp vào account messaging",
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
            "Messaging từ chối gắn user tích hợp vào account",
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
        if not await is_platform_admin(current_user, db):
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
                "Chưa có map messaging account cho tenant này",
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
                "messaging_account": data,
            }
            if sanitize_meta:
                ok_data["payload_sanitize_meta"] = sanitize_meta
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Cập nhật messaging account thành công",
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
            "Cập nhật messaging account thất bại",
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
        if not await is_platform_admin(current_user, db):
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
                "Chưa có map messaging account cho tenant này",
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
                "Xóa account trên messaging thất bại",
                _chatwoot_error_payload(res),
            )

        await _delete_tenant_agent_and_bot_maps(db, tenant_id)
        await db.delete(mapping)
        await db.commit()
        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Đã xóa messaging account và bản ghi map",
            {"tenant_id": str(tenant_id), "removed_messaging_account_id": account_id},
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

async def _build_bulk_action_payload(
    body: ChatwootBulkActionLabelsBody,
    db: AsyncSession,
    tenant_id: UUID,
) -> tuple[dict[str, Any] | None, Any]:
    """
    Build payload bulk_actions đúng shape Chatwoot:
    - luôn: type, ids
    - labels / fields chỉ khi có nội dung (không gửi {})
    - fields.assignee_id: map UUID nội bộ → id remote nếu là UUID
    - fields.team_id: map UUID team nội bộ → id remote nếu là UUID
    """
    payload: dict[str, Any] = {
        "type": body.type,
        "ids": list(body.ids),
    }

    if body.labels:
        # bỏ key rỗng trong labels (add/remove)
        labels = {k: v for k, v in body.labels.items() if v}
        if labels:
            payload["labels"] = labels

    if body.fields:
        fields = dict(body.fields)

        # assignee_agent_uuid (alias) hoặc assignee_id dạng UUID nội bộ
        assignee_uuid_raw = fields.pop("assignee_agent_uuid", None)
        if assignee_uuid_raw is None and "assignee_id" in fields:
            raw = fields.get("assignee_id")
            if isinstance(raw, UUID) or (
                isinstance(raw, str) and _looks_like_uuid(raw)
            ):
                assignee_uuid_raw = raw
                fields.pop("assignee_id", None)

        if assignee_uuid_raw is not None:
            try:
                assignee_uuid = (
                    assignee_uuid_raw
                    if isinstance(assignee_uuid_raw, UUID)
                    else UUID(str(assignee_uuid_raw))
                )
            except (TypeError, ValueError):
                return None, api_response(
                    ResponseStatus.ERROR,
                    ResponseStatusCode.BAD_REQUEST,
                    "assignee_id / assignee_agent_uuid không hợp lệ",
                )
            m = await _map_tenant_agent_by_local(db, tenant_id, assignee_uuid)
            if not m:
                # fallback USER map
                remote_ids, missing = await _translate_local_agent_uuids_to_remote(
                    db, tenant_id, [assignee_uuid]
                )
                if missing or not remote_ids:
                    return None, api_response(
                        ResponseStatus.ERROR,
                        ResponseStatusCode.NOT_FOUND,
                        f"Không tìm thấy map agent cho UUID: {assignee_uuid}",
                    )
                fields["assignee_id"] = remote_ids[0]
            else:
                fields["assignee_id"] = int(m.chatwoot_id)

        team_raw = fields.get("team_id")
        if team_raw is not None and (
            isinstance(team_raw, UUID)
            or (isinstance(team_raw, str) and _looks_like_uuid(team_raw))
        ):
            try:
                team_uuid = team_raw if isinstance(team_raw, UUID) else UUID(str(team_raw))
            except (TypeError, ValueError):
                return None, api_response(
                    ResponseStatus.ERROR,
                    ResponseStatusCode.BAD_REQUEST,
                    "team_id không hợp lệ",
                )
            tm = await _map_tenant_team_by_local(db, tenant_id, team_uuid)
            if not tm:
                return None, api_response(
                    ResponseStatus.ERROR,
                    ResponseStatusCode.NOT_FOUND,
                    f"Không tìm thấy map team cho UUID: {team_uuid}",
                )
            fields["team_id"] = int(tm.chatwoot_id)

        if fields:
            payload["fields"] = fields

    return payload, None


def _looks_like_uuid(value: str) -> bool:
    try:
        UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


async def bulk_action_account(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    body: ChatwootBulkActionLabelsBody,
    db: AsyncSession,
):
    """
    Forward bulk actions sang messaging.

    Payload gửi upstream (chỉ key có dữ liệu):
    {
        "type": "Conversation",
        "ids": [53],
        "fields": { "assignee_id": 2 }
    }
    hoặc labels:
    {
        "type": "Conversation",
        "ids": [35],
        "labels": { "remove": ["test"] }
    }
    """
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

        payload, err = await _build_bulk_action_payload(body, db, tenant_id)
        if err is not None:
            return err

        pairs = _forward_all_query_pairs(request)

        res = await chatwoot_client.application_request(
            "POST",
            f"/api/v1/accounts/{account_id}/bulk_actions",
            json_body=payload,
            params=pairs or None,
        )

        data = res.data
        if res.status_code in (200, 201):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Bulk action messaging thành công",
                {
                    "tenant_id": str(tenant_id),
                    "messaging_account_id": account_id,
                    "result": data,
                },
            )

        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Bulk action messaging thất bại",
            _chatwoot_error_payload(
                res,
                sent_payload_keys=sorted(payload.keys(), key=str),
            ),
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

async def add_new_agent_inboxes(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    body: ChatwootActionAgentInboxesBody,
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

        remote_user_ids, missing_uuids = await _translate_local_agent_uuids_to_remote(
            db, tenant_id, body.user_ids
        )
        if missing_uuids:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                f"Không tìm thấy map agent cho các UUID sau: {', '.join(missing_uuids)}",
            )

        payload = {
            "inbox_id": body.inbox_id,
            "user_ids": remote_user_ids,
        }

        pairs = _forward_all_query_pairs(request)

        res = await chatwoot_client.application_request(
            "POST",
            f"/api/v1/accounts/{account_id}/inbox_members",
            json_body=payload,
            params=pairs or None,
        )

        data = res.data
        if res.status_code in (200, 201):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Thêm agent vào inbox thành công",
                {
                    "tenant_id": str(tenant_id),
                    "messaging_account_id": account_id,
                    "result": data,
                },
            )

        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Thêm agent vào inbox thất bại",
            _chatwoot_error_payload(
                res,
                sent_payload_keys=sorted(payload.keys(), key=str),
            ),
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


async def patch_new_agent_inboxes(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    body: ChatwootActionAgentInboxesBody,
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

        remote_user_ids, missing_uuids = await _translate_local_agent_uuids_to_remote(
            db, tenant_id, body.user_ids
        )
        if missing_uuids:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                f"Không tìm thấy map agent cho các UUID sau: {', '.join(missing_uuids)}",
            )

        payload = {
            "inbox_id": body.inbox_id,
            "user_ids": remote_user_ids,
        }

        pairs = _forward_all_query_pairs(request)

        res = await chatwoot_client.application_request(
            "PATCH",
            f"/api/v1/accounts/{account_id}/inbox_members",
            json_body=payload,
            params=pairs or None,
        )

        data = res.data
        if res.status_code in (200, 201):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Cập nhật agent trong inbox thành công",
                {
                    "tenant_id": str(tenant_id),
                    "messaging_account_id": account_id,
                    "result": data,
                },
            )

        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Cập nhật agent trong inbox thất bại",
            _chatwoot_error_payload(
                res,
                sent_payload_keys=sorted(payload.keys(), key=str),
            ),
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


async def get_custom_filters(
    request: Request,
    current_user: User,
    tenant_id: UUID,
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

        pairs = _forward_all_query_pairs(request)

        res = await chatwoot_client.application_request(
            "GET",
            f"/api/v1/accounts/{account_id}/custom_filters",
            params=pairs or None,
        )

        data = res.data

        if res.status_code == 200:
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Lấy danh sách custom filters thành công",
                {
                    "tenant_id": str(tenant_id),
                    "messaging_account_id": account_id,
                    "custom_filters": data,
                },
            )

        return api_response(
            ResponseStatus.ERROR,
            res.status_code
            if res.status_code in (400, 401, 403, 404, 422, 503)
            else 502,
            "Không lấy được danh sách custom filters từ messaging",
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

async def custom_filters(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    body: ChatwootCustomFilterCreateBody,
    db: AsyncSession,
):
    """
    Forward custom filter sang messaging.

    Payload ví dụ:
    {
        "name": "Filter 1",
        "filter_type": 0,
        "query": {
            "payload": [
                {
                    "attribute_key": "status",
                    "attribute_model": "standard",
                    "filter_operator": "equal_to",
                    "values": [
                        "open",
                        "resolved"
                    ],
                    "custom_attribute_type": ""
                }
            ]
        }
    }
    """
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

        payload = body.model_dump(exclude_none=True)

        pairs = _forward_all_query_pairs(request)

        res = await chatwoot_client.application_request(
            "POST",
            f"/api/v1/accounts/{account_id}/custom_filters",
            json_body=payload,
            params=pairs or None,
        )

        data = res.data

        if res.status_code in (200, 201):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Tạo custom filter messaging thành công",
                {
                    "tenant_id": str(tenant_id),
                    "messaging_account_id": account_id,
                    "result": data,
                },
            )

        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (400, 401, 403, 404, 422, 503) else 502,
            "Tạo custom filter messaging thất bại",
            _chatwoot_error_payload(
                res,
                sent_payload_keys=sorted(payload.keys()),
            ),
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

async def update_custom_filter(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    filter_id: int,
    body: ChatwootCustomFiltersBody,
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

        payload = body.model_dump(exclude_none=True)

        pairs = _forward_all_query_pairs(request)

        res = await chatwoot_client.application_request(
            "PATCH",
            f"/api/v1/accounts/{account_id}/custom_filters/{filter_id}",
            json_body=payload,
            params=pairs or None,
        )

        data = res.data

        if res.status_code in (200, 201):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Cập nhật custom filter thành công",
                {
                    "tenant_id": str(tenant_id),
                    "messaging_account_id": account_id,
                    "filter_id": filter_id,
                    "result": data,
                },
            )

        return api_response(
            ResponseStatus.ERROR,
            res.status_code
            if res.status_code in (400, 401, 403, 404, 422, 503)
            else 502,
            "Cập nhật custom filter thất bại",
            _chatwoot_error_payload(
                res,
                sent_payload_keys=sorted(payload.keys()),
            ),
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

async def delete_custom_filter(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    filter_id: int,
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

        pairs = _forward_all_query_pairs(request)

        res = await chatwoot_client.application_request(
            "DELETE",
            f"/api/v1/accounts/{account_id}/custom_filters/{filter_id}",
            params=pairs or None,
        )

        if res.status_code in (200, 204):
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Xóa custom filter thành công",
                {
                    "tenant_id": str(tenant_id),
                    "messaging_account_id": account_id,
                    "filter_id": filter_id,
                },
            )

        return api_response(
            ResponseStatus.ERROR,
            res.status_code
            if res.status_code in (400, 401, 403, 404, 422, 503)
            else 502,
            "Xóa custom filter thất bại",
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