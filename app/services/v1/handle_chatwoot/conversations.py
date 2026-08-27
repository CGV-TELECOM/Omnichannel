from __future__ import annotations

import logging
from typing import Any, List, Tuple
from urllib.parse import quote
from uuid import UUID

from fastapi import Request, UploadFile
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession


from app.db.models import User
from app.integrations.chatwoot import client as chatwoot_client
from app.schemas.requests.chatwoot import (
    ChatwootApplicationJsonBody,
    ChatwootConversationAssignBody,
    ChatwootConversationCustomAttributesBody,
    ChatwootConversationLabelsMutationBody,
    ChatwootConversationToggleStatusBody,
    ChatwootConversationTypingBody,
    ConversationFilter,
    ConversationFilterRequest,
)
from app.schemas.responses.api_response_rule import (
    ResponseStatus,
    ResponseStatusCode,
    api_response,
)

from app.services.v1.handle_chatwoot._shared import (
    _chatwoot_agent_id_to_local_map,
    _chatwoot_error_payload,
    _forward_all_query_pairs,
    _map_tenant_agent_by_local,
    _map_tenant_team_by_local,
    _redact_chatwoot_agent_like_user,
    _require_tenant_access,
    _resolve_account_id,
    _tenant_application_forward,
    _walk_redact_agent_refs,
)

logger = logging.getLogger(__name__)


async def list_conversations(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """
    GET /api/v1/accounts/{account_id}/conversations — [Conversations List](https://developers.chatwoot.com/api-reference/conversations/conversations-list).
    """
    try:
        denied = await _require_tenant_access(current_user, tenant_id, db)
        if denied is not None:
            return denied
        account_id, _ = await _resolve_account_id(db, tenant_id)
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Chưa có map messaging account cho tenant này",
            )
        raw_pairs = _forward_all_query_pairs(request)
        pairs: list[tuple[str, str]] = []
        for k, v in raw_pairs:
            if k == "team_id":
                try:
                    team_uuid = UUID(v)
                    tm = await _map_tenant_team_by_local(db, tenant_id, team_uuid)
                    if not tm:
                        return api_response(
                            ResponseStatus.ERROR,
                            ResponseStatusCode.NOT_FOUND,
                            "Không có map team cho UUID này (gọi GET teams để tạo map hoặc tạo team)",
                        )
                    pairs.append((k, str(tm.chatwoot_id)))
                except ValueError:
                    pairs.append((k, v))
            elif k == "assignee_id":
                try:
                    agent_uuid = UUID(v)
                    am = await _map_tenant_agent_by_local(db, tenant_id, agent_uuid)
                    if not am:
                        return api_response(
                            ResponseStatus.ERROR,
                            ResponseStatusCode.NOT_FOUND,
                            "Không có map agent cho UUID này (gọi GET agents để tạo map hoặc tạo agent)",
                        )
                    pairs.append((k, str(am.chatwoot_id)))
                except ValueError:
                    pairs.append((k, v))
            else:
                pairs.append((k, v))

        res = await chatwoot_client.application_request(
            "GET",
            f"/api/v1/accounts/{account_id}/conversations",
            params=pairs or None,
        )
        cw_map = await _chatwoot_agent_id_to_local_map(db, tenant_id)
        if res.status_code == 200:
            data = _walk_redact_agent_refs(res.data, cw_map)
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Danh sách conversation messaging (agent id đã map sang UUID)",
                {
                    "tenant_id": str(tenant_id),
                    "messaging": data,
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (400, 401, 403, 404, 503) else 502,
            "Không lấy được danh sách conversation từ messaging",
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


async def filter_conversations(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    body: ConversationFilterRequest,
    db: AsyncSession,
):
    """
    POST /api/v1/accounts/{account_id}/conversations/filter
    """
    try:
        denied = await _require_tenant_access(current_user, tenant_id, db)
        if denied is not None:
            return denied

        account_id, _ = await _resolve_account_id(db, tenant_id)
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Chưa có map messaging account cho tenant này",
            )

        res = await chatwoot_client.application_request(
            "POST",
            f"/api/v1/accounts/{account_id}/conversations/filter",
            params=_forward_all_query_pairs(request) or None,
            json_body=body.model_dump(exclude_none=True),
        )

        cw_map = await _chatwoot_agent_id_to_local_map(db, tenant_id)

        if res.status_code == 200:
            data = _walk_redact_agent_refs(res.data, cw_map)

            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Lọc danh sách conversation thành công",
                {
                    "tenant_id": str(tenant_id),
                    "messaging": data,
                },
            )

        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (400, 401, 403, 404, 503) else 502,
            "Không lọc được danh sách conversation từ messaging",
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

async def get_conversation(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    db: AsyncSession,
):
    """
    GET /api/v1/accounts/{account_id}/conversations/{conversation_id} — [Conversation Details](https://developers.chatwoot.com/api-reference/conversations/conversation-details).
    """
    try:
        denied = await _require_tenant_access(current_user, tenant_id, db)
        if denied is not None:
            return denied
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
            f"/api/v1/accounts/{account_id}/conversations/{conversation_id}",
            params=pairs or None,
        )
        cw_map = await _chatwoot_agent_id_to_local_map(db, tenant_id)
        if res.status_code == 200:
            data = _walk_redact_agent_refs(res.data, cw_map)
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Chi tiết conversation messaging (agent id đã map sang UUID)",
                {
                    "tenant_id": str(tenant_id),
                    "conversation_id": conversation_id,
                    "messaging": data,
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 503) else 502,
            "Không lấy được conversation từ messaging",
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

async def delete_conversation(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    db: AsyncSession,
):
    """DELETE .../accounts/{account_id}/conversations/{conversation_id}"""
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="DELETE",
        path_suffix=f"/conversations/{conversation_id}",
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Đã xóa conversation trên messaging",
        success_codes=frozenset({200, 204}),
        extra_response={
            "conversation_id": conversation_id,
        },
        error_message="Xóa conversation trên messaging thất bại",
    )




async def list_conversation_messages(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    db: AsyncSession,
):
    """
    GET /api/v1/accounts/{account_id}/conversations/{conversation_id}/messages — [Get messages](https://developers.chatwoot.com/api-reference/messages/get-messages).
    """
    try:
        denied = await _require_tenant_access(current_user, tenant_id, db)
        if denied is not None:
            return denied
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
            f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages",
            params=pairs or None,
        )
        cw_map = await _chatwoot_agent_id_to_local_map(db, tenant_id)
        if res.status_code == 200:
            data = _walk_redact_agent_refs(res.data, cw_map)
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Danh sách tin nhắn conversation (agent id đã map sang UUID)",
                {
                    "tenant_id": str(tenant_id),
                    "conversation_id": conversation_id,
                    "messaging": data,
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Không lấy được messages từ messaging",
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


async def assign_conversation(
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootConversationAssignBody,
    db: AsyncSession,
):
    """
    POST /api/v1/accounts/{account_id}/conversations/{conversation_id}/assignments
    — [Assign Conversation](https://developers.chatwoot.com/api-reference/conversation-assignments/assign-conversation).

    Sau assign thành công: sync bot flags theo assignee (người → tắt bot, AI Bot → bật).
    """
    try:
        denied = await _require_tenant_access(current_user, tenant_id, db)
        if denied is not None:
            return denied
        account_id, _ = await _resolve_account_id(db, tenant_id)
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Chưa có map messaging account cho tenant này",
            )
        payload: dict[str, Any] = {}
        if body.assignee_agent_uuid is not None:
            m = await _map_tenant_agent_by_local(db, tenant_id, body.assignee_agent_uuid)
            if not m:
                return api_response(
                    ResponseStatus.ERROR,
                    ResponseStatusCode.NOT_FOUND,
                    "Không có map agent cho UUID này (gọi GET agents để tạo map hoặc tạo agent)",
                )
            payload["assignee_id"] = m.chatwoot_id
        if body.team_id is not None:
            tm = await _map_tenant_team_by_local(db, tenant_id, body.team_id)
            if not tm:
                return api_response(
                    ResponseStatus.ERROR,
                    ResponseStatusCode.NOT_FOUND,
                    "Không có map team cho UUID này (gọi GET teams để tạo map hoặc tạo team)",
                )
            if body.assignee_agent_uuid is None:
                payload["team_id"] = tm.chatwoot_id

        res = await chatwoot_client.application_request(
            "POST",
            f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/assignments",
            json_body=payload,
        )
        cw_map = await _chatwoot_agent_id_to_local_map(db, tenant_id)
        if res.status_code == 200 and isinstance(res.data, dict):
            # Sync bot control ngay (không chờ webhook) — bot ids theo tenant
            from app.services.v1.handle_chatwoot.chatbot import (
                coerce_assignee_id,
                sync_bot_flags_for_assignee,
            )

            assigned_id = payload.get("assignee_id")
            if assigned_id is None and isinstance(res.data, dict):
                assigned_id = coerce_assignee_id(res.data)
            try:
                await sync_bot_flags_for_assignee(
                    db,
                    tenant_id,
                    int(account_id),
                    int(conversation_id),
                    coerce_assignee_id(assigned_id),
                    send_note=True,
                )
            except Exception as sync_err:
                logger.warning(
                    "Sync bot flags sau assign thất bại conv=%s: %s",
                    conversation_id,
                    sync_err,
                )

            out_data = _redact_chatwoot_agent_like_user(res.data, cw_map)
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã assign conversation trên messaging",
                {
                    "tenant_id": str(tenant_id),
                    "conversation_id": conversation_id,
                    "messaging": out_data,
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 422, 503) else 502,
            "Assign conversation trên messaging thất bại",
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


async def assign_conversation_to_ai_bot(
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    db: AsyncSession,
):
    """
    Handback: giao lại conversation cho AI Bot mặc định của tenant
    (messaging_bots is_default).
    """
    try:
        denied = await _require_tenant_access(current_user, tenant_id, db)
        if denied is not None:
            return denied
        account_id, _ = await _resolve_account_id(db, tenant_id)
        if account_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Chưa có map messaging account cho tenant này",
            )

        from app.db.models import Tenant
        from app.services.v1.handle_chatwoot.chatbot import (
            assign_to_ai_bot,
            default_bot_agent_uuid,
            resolve_default_bot_chatwoot_id,
        )

        tenant = await db.get(Tenant, tenant_id)
        if tenant is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không tìm thấy tenant",
            )

        meta = tenant.meta_data if isinstance(tenant.meta_data, dict) else {}
        if meta.get("chatbot_enabled") is False:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                (
                    "Chatbot đang tắt (chatbot_enabled=false). "
                    "Bật lại trong PATCH /tenants/me/settings trước khi giao cho bot."
                ),
            )

        bot_cw_id = await resolve_default_bot_chatwoot_id(db, tenant)
        if bot_cw_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                (
                    "Tenant chưa cấu hình AI Bot (messaging_bots trống hoặc thiếu "
                    "is_default). Chọn agent từ GET messaging agents rồi PATCH "
                    "/tenants/me/settings."
                ),
            )

        ok, detail = await assign_to_ai_bot(
            db,
            tenant,
            int(account_id),
            int(conversation_id),
            sync_flags=True,
            send_note=True,
        )
        if not ok:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.INTERNAL_SERVER_ERROR
                if detail.startswith("assign_failed")
                else ResponseStatusCode.BAD_REQUEST,
                "Assign AI Bot thất bại",
                {"detail": detail},
            )
        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Đã giao conversation cho AI Bot",
            {
                "tenant_id": str(tenant_id),
                "conversation_id": conversation_id,
                "assignee_id": bot_cw_id,
                "agent_uuid": (
                    str(default_bot_agent_uuid(meta))
                    if default_bot_agent_uuid(meta)
                    else None
                ),
                "bot_active": True,
            },
        )
    except Exception as e:
        logger.exception("assign_conversation_to_ai_bot: %s", e)
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi không xác định: {e}",
        )


# --- Inbox / team / conversation / message forward (Application API) ---


async def list_inboxes(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """GET /api/v1/accounts/{account_id}/inboxes — listAllInboxes."""
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="GET",
        path_suffix="/inboxes",
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Danh sách inbox messaging",
        error_message="Không lấy được danh sách inbox từ messaging",
    )


async def create_inbox(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    body: ChatwootApplicationJsonBody,
    db: AsyncSession,
):
    """POST /api/v1/accounts/{account_id}/inboxes — inboxCreation."""
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="POST",
        path_suffix="/inboxes",
        json_body=payload,
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Đã tạo inbox trên messaging",
        success_codes=frozenset({200, 201}),
        error_message="Tạo inbox trên messaging thất bại",
        error_payload_keys=sorted(payload.keys(), key=str),
    )


async def get_inbox(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    inbox_id: int,
    db: AsyncSession,
):
    """GET /api/v1/accounts/{account_id}/inboxes/{id}."""
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="GET",
        path_suffix=f"/inboxes/{inbox_id}",
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Chi tiết inbox messaging",
        error_message="Không lấy được inbox từ messaging",
    )


async def update_inbox(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    inbox_id: int,
    body: ChatwootApplicationJsonBody,
    db: AsyncSession,
):
    """PATCH /api/v1/accounts/{account_id}/inboxes/{id}."""
    payload = body.model_dump(mode="json", exclude_unset=True, exclude_none=True)
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="PATCH",
        path_suffix=f"/inboxes/{inbox_id}",
        json_body=payload,
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Đã cập nhật inbox trên messaging",
        error_message="Cập nhật inbox trên messaging thất bại",
        error_payload_keys=sorted(payload.keys(), key=str),
    )


async def create_conversation(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    body: ChatwootApplicationJsonBody,
    db: AsyncSession,
):
    """POST /api/v1/accounts/{account_id}/conversations — newConversation."""
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="POST",
        path_suffix="/conversations",
        json_body=payload,
        forward_all_query_params=True,
        redact_agents=True,
        ok_message="Đã tạo conversation trên messaging",
        success_codes=frozenset({200, 201}),
        error_message="Tạo conversation trên messaging thất bại",
        error_payload_keys=sorted(payload.keys(), key=str),
    )


async def update_conversation(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootApplicationJsonBody,
    db: AsyncSession,
):
    """PATCH /api/v1/accounts/{account_id}/conversations/{conversation_id}."""
    payload = body.model_dump(mode="json", exclude_unset=True, exclude_none=True)
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="PATCH",
        path_suffix=f"/conversations/{conversation_id}",
        json_body=payload,
        forward_all_query_params=True,
        redact_agents=True,
        ok_message="Đã cập nhật conversation trên messaging",
        extra_response={"conversation_id": conversation_id},
        error_message="Cập nhật conversation trên messaging thất bại",
        error_payload_keys=sorted(payload.keys(), key=str),
    )


async def create_conversation_message(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootApplicationJsonBody,
    db: AsyncSession,
):
    """POST /api/v1/accounts/{account_id}/conversations/{conversation_id}/messages — create message."""
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="POST",
        path_suffix=f"/conversations/{conversation_id}/messages",
        json_body=payload,
        forward_all_query_params=True,
        redact_agents=True,
        ok_message="Đã gửi message lên messaging",
        success_codes=frozenset({200, 201}),
        extra_response={"conversation_id": conversation_id},
        error_message="Gửi message lên messaging thất bại",
        error_payload_keys=sorted(payload.keys(), key=str),
    )




async def delete_conversation_message(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    message_id: int,
    db: AsyncSession,
):
    """DELETE .../conversations/{conversation_id}/messages/{message_id}."""
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="DELETE",
        path_suffix=f"/conversations/{conversation_id}/messages/{message_id}",
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Đã xóa message trên messaging",
        success_codes=frozenset({200, 204}),
        extra_response={
            "conversation_id": conversation_id,
            "message_id": message_id,
        },
        error_message="Xóa message trên messaging thất bại",
    )


async def toggle_conversation_status(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootConversationToggleStatusBody,
    db: AsyncSession,
):
    """POST .../toggle_status — toggle-status-of-a-conversation."""
    payload = body.model_dump(mode="json", exclude_none=True)
    result = await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="POST",
        path_suffix=f"/conversations/{conversation_id}/toggle_status",
        json_body=payload,
        forward_all_query_params=True,
        redact_agents=True,
        ok_message="Đã đổi trạng thái conversation trên messaging",
        extra_response={"conversation_id": conversation_id},
        error_message="Đổi trạng thái conversation thất bại",
        error_payload_keys=sorted(payload.keys(), key=str),
    )
    # MVP CSAT: sau khi resolve thành công → tạo + gửi link (idempotent với webhook)
    if (
        body.status == "resolved"
        and isinstance(result, dict)
        and result.get("status") == ResponseStatus.SUCCESS.value
    ):
        try:
            from app.services.v1.handle_conversation_rating import (
                fetch_channel_and_send_on_resolve,
            )

            account_id, _ = await _resolve_account_id(db, tenant_id)
            if account_id is not None:
                await fetch_channel_and_send_on_resolve(
                    db,
                    tenant_id=tenant_id,
                    messaging_account_id=int(account_id),
                    conversation_id=int(conversation_id),
                )
        except Exception as e:
            logger.warning("CSAT sau toggle_status thất bại (không ảnh hưởng API): %s", e)
    return result


async def get_conversation_labels(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    db: AsyncSession,
):
    """GET .../conversations/{conversation_id}/labels."""
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="GET",
        path_suffix=f"/conversations/{conversation_id}/labels",
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Danh sách label của conversation",
        extra_response={"conversation_id": conversation_id},
        error_message="Không lấy được label conversation từ messaging",
    )


async def list_labels(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """GET .../labels — list labels/tags ở mức account."""
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="GET",
        path_suffix="/labels",
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Danh sách labels của account messaging",
        extra_response={"tenant_id": str(tenant_id)},
        error_message="Không lấy được danh sách labels từ messaging",
    )


async def create_label(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    body: ChatwootApplicationJsonBody,
    db: AsyncSession,
):
    """POST .../labels — tạo label/tag ở mức account."""
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="POST",
        path_suffix="/labels",
        json_body=payload,
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Đã tạo label trên messaging",
        extra_response={"tenant_id": str(tenant_id)},
        error_message="Tạo label trên messaging thất bại",
        error_payload_keys=sorted(payload.keys(), key=str),
    )


async def delete_label(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    label: str,
    db: AsyncSession,
):
    """DELETE .../labels/{title} — xóa label/tag theo title."""
    encoded_label = quote(label, safe="")
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="DELETE",
        path_suffix=f"/labels/{encoded_label}",
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Đã xóa label trên messaging",
        extra_response={"tenant_id": str(tenant_id), "label": label},
        error_message="Xóa label trên messaging thất bại",
    )


async def set_conversation_labels(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootConversationLabelsMutationBody,
    db: AsyncSession,
):
    """POST .../conversations/{conversation_id}/labels — ghi đè labels."""
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="POST",
        path_suffix=f"/conversations/{conversation_id}/labels",
        json_body=payload,
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Đã cập nhật label conversation trên messaging",
        extra_response={"conversation_id": conversation_id},
        error_message="Cập nhật label conversation thất bại",
        error_payload_keys=sorted(payload.keys(), key=str),
    )


async def toggle_conversation_typing(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootConversationTypingBody,
    db: AsyncSession,
):
    """POST .../toggle_typing_status."""
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="POST",
        path_suffix=f"/conversations/{conversation_id}/toggle_typing_status",
        json_body=payload,
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Đã gửi typing status lên messaging",
        extra_response={"conversation_id": conversation_id},
        error_message="Gửi typing status thất bại",
        error_payload_keys=sorted(payload.keys(), key=str),
    )


async def update_conversation_custom_attributes(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootConversationCustomAttributesBody,
    db: AsyncSession,
):
    """POST .../custom_attributes."""
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="POST",
        path_suffix=f"/conversations/{conversation_id}/custom_attributes",
        json_body=payload,
        forward_all_query_params=True,
        redact_agents=True,
        ok_message="Đã cập nhật custom_attributes conversation trên messaging",
        extra_response={"conversation_id": conversation_id},
        error_message="Cập nhật custom_attributes conversation thất bại",
        error_payload_keys=sorted(payload.keys(), key=str),
    )

async def get_attachment(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    db: AsyncSession,
):
    """GET .../conversations/{conversation_id}/messages/{message_id}/attachments/{attachment_id}."""
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="GET",
        path_suffix=f"/conversations/{conversation_id}/attachments",
        forward_all_query_params=True,
        redact_agents=False,
        ok_message="Chi tiết attachment messaging",
        error_message="Không lấy được attachment từ messaging",
    )

async def update_last_seen(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    db: AsyncSession,
):
    """POST .../update_last_seen."""
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="POST",
        path_suffix=f"/conversations/{conversation_id}/update_last_seen",
        forward_all_query_params=True,
        redact_agents=True,
        ok_message="Đã cập nhật last_seen conversation trên messaging",
        extra_response={"conversation_id": conversation_id},
        error_message="Cập nhật last_seen conversation thất bại",
    )