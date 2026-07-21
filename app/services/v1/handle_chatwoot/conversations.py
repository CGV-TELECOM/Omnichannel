from __future__ import annotations

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
                "Chưa có map Chatwoot account cho tenant này",
            )
        pairs = _forward_all_query_pairs(request)
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
                "Danh sách conversation Chatwoot (agent id đã map sang UUID)",
                {
                    "tenant_id": str(tenant_id),
                    "chatwoot": data,
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (400, 401, 403, 404, 503) else 502,
            "Không lấy được danh sách conversation từ Chatwoot",
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
                "Chưa có map Chatwoot account cho tenant này",
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
                    "chatwoot": data,
                },
            )

        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (400, 401, 403, 404, 503) else 502,
            "Không lọc được danh sách conversation từ Chatwoot",
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
                "Chưa có map Chatwoot account cho tenant này",
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
                "Chi tiết conversation Chatwoot (agent id đã map sang UUID)",
                {
                    "tenant_id": str(tenant_id),
                    "conversation_id": conversation_id,
                    "chatwoot": data,
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 403, 404, 503) else 502,
            "Không lấy được conversation từ Chatwoot",
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
        ok_message="Đã xóa conversation trên Chatwoot",
        success_codes=frozenset({200, 204}),
        extra_response={
            "conversation_id": conversation_id,
        },
        error_message="Xóa conversation trên Chatwoot thất bại",
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
                "Chưa có map Chatwoot account cho tenant này",
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
                    "chatwoot": data,
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 503) else 502,
            "Không lấy được messages từ Chatwoot",
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
    print(f"Vào đây")
    """
    POST /api/v1/accounts/{account_id}/conversations/{conversation_id}/assignments
    — [Assign Conversation](https://developers.chatwoot.com/api-reference/conversation-assignments/assign-conversation).
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
                "Chưa có map Chatwoot account cho tenant này",
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

        print(f"Check team_id: {body.team_id}")
        res = await chatwoot_client.application_request(
            "POST",
            f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/assignments",
            json_body=payload,
        )
        cw_map = await _chatwoot_agent_id_to_local_map(db, tenant_id)
        if res.status_code == 200 and isinstance(res.data, dict):
            out_data = _redact_chatwoot_agent_like_user(res.data, cw_map)
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Đã assign conversation trên Chatwoot",
                {
                    "tenant_id": str(tenant_id),
                    "conversation_id": conversation_id,
                    "chatwoot": out_data,
                },
            )
        return api_response(
            ResponseStatus.ERROR,
            res.status_code if res.status_code in (401, 404, 422, 503) else 502,
            "Assign conversation trên Chatwoot thất bại",
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
        ok_message="Danh sách inbox Chatwoot",
        error_message="Không lấy được danh sách inbox từ Chatwoot",
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
        ok_message="Đã tạo inbox trên Chatwoot",
        success_codes=frozenset({200, 201}),
        error_message="Tạo inbox trên Chatwoot thất bại",
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
        ok_message="Chi tiết inbox Chatwoot",
        error_message="Không lấy được inbox từ Chatwoot",
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
        ok_message="Đã cập nhật inbox trên Chatwoot",
        error_message="Cập nhật inbox trên Chatwoot thất bại",
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
        ok_message="Đã tạo conversation trên Chatwoot",
        success_codes=frozenset({200, 201}),
        error_message="Tạo conversation trên Chatwoot thất bại",
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
        ok_message="Đã cập nhật conversation trên Chatwoot",
        extra_response={"conversation_id": conversation_id},
        error_message="Cập nhật conversation trên Chatwoot thất bại",
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
        ok_message="Đã gửi message lên Chatwoot",
        success_codes=frozenset({200, 201}),
        extra_response={"conversation_id": conversation_id},
        error_message="Gửi message lên Chatwoot thất bại",
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
        ok_message="Đã xóa message trên Chatwoot",
        success_codes=frozenset({200, 204}),
        extra_response={
            "conversation_id": conversation_id,
            "message_id": message_id,
        },
        error_message="Xóa message trên Chatwoot thất bại",
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
    return await _tenant_application_forward(
        current_user,
        tenant_id,
        db,
        request=request,
        method="POST",
        path_suffix=f"/conversations/{conversation_id}/toggle_status",
        json_body=payload,
        forward_all_query_params=True,
        redact_agents=True,
        ok_message="Đã đổi trạng thái conversation trên Chatwoot",
        extra_response={"conversation_id": conversation_id},
        error_message="Đổi trạng thái conversation thất bại",
        error_payload_keys=sorted(payload.keys(), key=str),
    )


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
        error_message="Không lấy được label conversation từ Chatwoot",
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
        ok_message="Danh sách labels của account Chatwoot",
        extra_response={"tenant_id": str(tenant_id)},
        error_message="Không lấy được danh sách labels từ Chatwoot",
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
        ok_message="Đã tạo label trên Chatwoot",
        extra_response={"tenant_id": str(tenant_id)},
        error_message="Tạo label trên Chatwoot thất bại",
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
        ok_message="Đã xóa label trên Chatwoot",
        extra_response={"tenant_id": str(tenant_id), "label": label},
        error_message="Xóa label trên Chatwoot thất bại",
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
        ok_message="Đã cập nhật label conversation trên Chatwoot",
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
        ok_message="Đã gửi typing status lên Chatwoot",
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
        ok_message="Đã cập nhật custom_attributes conversation trên Chatwoot",
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
        ok_message="Chi tiết attachment Chatwoot",
        error_message="Không lấy được attachment từ Chatwoot",
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
        ok_message="Đã cập nhật last_seen conversation trên Chatwoot",
        extra_response={"conversation_id": conversation_id},
        error_message="Cập nhật last_seen conversation thất bại",
    )