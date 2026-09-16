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
from app.services.v1.handle_chatwoot.user_tokens import (
    ensure_user_chatwoot_api_token,
    missing_token_api_response,
    resolve_agent_scoped_access_token,
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
                "Doanh nghiệp chưa được liên kết kênh trò chuyện.",
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
                            "Không tìm thấy nhóm tương ứng.",
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
                            "Không tìm thấy nhân viên tương ứng.",
                        )
                    pairs.append((k, str(am.chatwoot_id)))
                except ValueError:
                    pairs.append((k, v))
            else:
                pairs.append((k, v))

        user_token, tok_err = await resolve_agent_scoped_access_token(db, current_user)
        if tok_err is not None:
            return tok_err

        res = await chatwoot_client.application_request(
            "GET",
            f"/api/v1/accounts/{account_id}/conversations",
            params=pairs or None,
            access_token=user_token,
        )
        cw_map = await _chatwoot_agent_id_to_local_map(db, tenant_id)
        if res.status_code == 200:
            data = _walk_redact_agent_refs(res.data, cw_map)
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Danh sách hội thoại",
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
                "Doanh nghiệp chưa được liên kết kênh trò chuyện.",
            )

        user_token, tok_err = await resolve_agent_scoped_access_token(db, current_user)
        if tok_err is not None:
            return tok_err

        res = await chatwoot_client.application_request(
            "POST",
            f"/api/v1/accounts/{account_id}/conversations/filter",
            params=_forward_all_query_pairs(request) or None,
            json_body=body.model_dump(exclude_none=True),
            access_token=user_token,
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
                "Doanh nghiệp chưa được liên kết kênh trò chuyện.",
            )
        pairs = _forward_all_query_pairs(request)
        user_token, tok_err = await resolve_agent_scoped_access_token(db, current_user)
        if tok_err is not None:
            return tok_err
        res = await chatwoot_client.application_request(
            "GET",
            f"/api/v1/accounts/{account_id}/conversations/{conversation_id}",
            params=pairs or None,
            access_token=user_token,
        )
        cw_map = await _chatwoot_agent_id_to_local_map(db, tenant_id)
        if res.status_code == 200:
            data = _walk_redact_agent_refs(res.data, cw_map)
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Chi tiết hội thoại",
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
        agent_scoped=True,
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
                "Doanh nghiệp chưa được liên kết kênh trò chuyện.",
            )
        pairs = _forward_all_query_pairs(request)
        user_token, tok_err = await resolve_agent_scoped_access_token(db, current_user)
        if tok_err is not None:
            return tok_err
        res = await chatwoot_client.application_request(
            "GET",
            f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages",
            params=pairs or None,
            access_token=user_token,
        )
        cw_map = await _chatwoot_agent_id_to_local_map(db, tenant_id)
        if res.status_code == 200:
            data = _walk_redact_agent_refs(res.data, cw_map)
            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Danh sách tin nhắn",
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

    RBAC: assign_messaging_conversation (self) + reassign_messaging_conversation
    (gán người khác / team, Chatwoot tự enforce). Sau assign: sync bot flags.
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
                "Doanh nghiệp chưa được liên kết kênh trò chuyện.",
            )
        payload: dict[str, Any] = {}
        if body.assignee_agent_uuid is not None:
            m = await _map_tenant_agent_by_local(db, tenant_id, body.assignee_agent_uuid)
            if not m:
                return api_response(
                    ResponseStatus.ERROR,
                    ResponseStatusCode.NOT_FOUND,
                    "Không tìm thấy nhân viên tương ứng.",
                )
            payload["assignee_id"] = m.chatwoot_id
        if body.team_id is not None:
            tm = await _map_tenant_team_by_local(db, tenant_id, body.team_id)
            if not tm:
                return api_response(
                    ResponseStatus.ERROR,
                    ResponseStatusCode.NOT_FOUND,
                    "Không tìm thấy nhóm tương ứng.",
                )
            if body.assignee_agent_uuid is None:
                payload["team_id"] = tm.chatwoot_id

        from app.services.v1.handle_chatwoot.user_tokens import (
            deny_unless_can_assign_assignee,
        )

        assign_denied = await deny_unless_can_assign_assignee(
            db,
            current_user,
            target_chatwoot_user_id=(
                int(payload["assignee_id"]) if "assignee_id" in payload else None
            ),
            assigning_team="team_id" in payload and "assignee_id" not in payload,
        )
        if assign_denied is not None:
            return assign_denied

        user_token, tok_err = await resolve_agent_scoped_access_token(db, current_user)
        if tok_err is not None:
            return tok_err

        res = await chatwoot_client.application_request(
            "POST",
            f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/assignments",
            json_body=payload,
            access_token=user_token,
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
                    access_token=user_token,
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
                "Doanh nghiệp chưa được liên kết kênh trò chuyện.",
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
                    "Trả lời tự động đang tắt. "
                    "Vui lòng bật chatbot trong cài đặt trước khi giao cho bot."
                ),
            )

        bot_cw_id = await resolve_default_bot_chatwoot_id(db, tenant)
        if bot_cw_id is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                (
                    "Chưa cấu hình chatbot AI cho doanh nghiệp. "
                    "Vui lòng thiết lập trong phần cài đặt."
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


async def sync_inbox_bindings(
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """Admin: GET inboxes Chatwoot → upsert messaging_inbox_bindings."""
    from app.services.v1.handle_chatwoot._shared import (
        _require_tenant_access,
        _resolve_account_id,
    )
    from app.services.v1.handle_messaging_inbox_binding import sync_tenant_inbox_bindings

    denied = await _require_tenant_access(current_user, tenant_id, db)
    if denied is not None:
        return denied
    account_id, _ = await _resolve_account_id(db, tenant_id)
    if account_id is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.NOT_FOUND,
            "Doanh nghiệp chưa được liên kết kênh trò chuyện.",
        )
    try:
        n = await sync_tenant_inbox_bindings(
            db,
            tenant_id=tenant_id,
            messaging_account_id=int(account_id),
        )
        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Đồng bộ inbox bindings thành công",
            {
                "tenant_id": str(tenant_id),
                "messaging_account_id": int(account_id),
                "upserted": n,
            },
        )
    except Exception as e:
        logger.exception("sync_inbox_bindings: %s", e)
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            f"Lỗi đồng bộ inbox bindings: {e}",
        )


async def list_inboxes(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    db: AsyncSession,
):
    """GET /api/v1/accounts/{account_id}/inboxes — listAllInboxes + sync website_token bindings."""
    from app.services.v1.handle_chatwoot._shared import _resolve_account_id
    from app.services.v1.handle_messaging_inbox_binding import (
        upsert_inbox_bindings_from_payload,
    )

    result = await _tenant_application_forward(
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
        agent_scoped=True,
    )
    # Sync bindings best-effort (không fail list nếu sync lỗi)
    try:
        if isinstance(result, dict) and result.get("status") == "success":
            data = result.get("data") or {}
            messaging = data.get("messaging") if isinstance(data, dict) else None
            account_id, _ = await _resolve_account_id(db, tenant_id)
            if account_id is not None and messaging is not None:
                n = await upsert_inbox_bindings_from_payload(
                    db,
                    tenant_id=tenant_id,
                    messaging_account_id=int(account_id),
                    inboxes_payload=messaging,
                )
                await db.commit()
                if isinstance(data, dict):
                    data["inbox_bindings_synced"] = n
    except Exception:
        logger.exception("Sync inbox bindings sau list_inboxes thất bại tenant=%s", tenant_id)
    return result


async def create_inbox(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    body: ChatwootApplicationJsonBody,
    db: AsyncSession,
):
    """POST /api/v1/accounts/{account_id}/inboxes — inboxCreation."""
    from app.services.v1.handle_chatwoot.contact_capture import (
        apply_contact_capture_to_inbox_payload,
        enrich_inbox_api_result,
    )

    payload = body.model_dump(mode="json", exclude_none=True)
    raw_capture = payload.get("contact_capture")
    # contact_capture → channel.pre_chat_*; hoist widget fields top-level → channel
    payload = apply_contact_capture_to_inbox_payload(payload)
    result = await _tenant_application_forward(
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
        agent_scoped=True,
    )
    result = enrich_inbox_api_result(result)
    await _persist_and_overlay_contact_capture(
        db,
        tenant_id=tenant_id,
        result=result,
        raw_capture=raw_capture,
    )
    return result


async def get_inbox(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    inbox_id: int,
    db: AsyncSession,
):
    """GET /api/v1/accounts/{account_id}/inboxes/{id}."""
    from app.services.v1.handle_chatwoot.contact_capture import enrich_inbox_api_result

    result = await _tenant_application_forward(
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
        agent_scoped=True,
    )
    result = enrich_inbox_api_result(result)
    await _overlay_stored_contact_capture(
        db, tenant_id=tenant_id, inbox_id=inbox_id, result=result
    )
    return result


async def update_inbox(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    inbox_id: int,
    body: ChatwootApplicationJsonBody,
    db: AsyncSession,
):
    """PATCH /api/v1/accounts/{account_id}/inboxes/{id}."""
    from app.services.v1.handle_chatwoot.contact_capture import (
        apply_contact_capture_to_inbox_payload,
        enrich_inbox_api_result,
    )

    payload = body.model_dump(mode="json", exclude_unset=True, exclude_none=True)
    raw_capture = payload.get("contact_capture")
    # contact_capture → channel.pre_chat_*; hoist widget fields top-level → channel
    payload = apply_contact_capture_to_inbox_payload(payload)
    result = await _tenant_application_forward(
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
        agent_scoped=True,
    )
    result = enrich_inbox_api_result(result)
    await _persist_and_overlay_contact_capture(
        db,
        tenant_id=tenant_id,
        result=result,
        raw_capture=raw_capture,
        inbox_id=inbox_id,
    )
    return result


def _inbox_obj_from_result(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict) or result.get("status") != "success":
        return None
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    messaging = data.get("messaging") if isinstance(data, dict) else None
    inbox_obj = (
        messaging.get("payload")
        if isinstance(messaging, dict) and isinstance(messaging.get("payload"), dict)
        else messaging
    )
    return inbox_obj if isinstance(inbox_obj, dict) else None


def _website_token_from_inbox_obj(inbox_obj: dict[str, Any]) -> str | None:
    if inbox_obj.get("website_token"):
        return str(inbox_obj["website_token"]).strip()
    channel = inbox_obj.get("channel")
    if isinstance(channel, dict):
        tok = channel.get("website_token") or channel.get("token")
        if tok:
            return str(tok).strip()
    return None


async def _persist_and_overlay_contact_capture(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    result: Any,
    raw_capture: Any,
    inbox_id: int | None = None,
) -> None:
    """
    Sau PATCH/POST thành công:
      - Persist policy vào DB binding (nếu FE gửi contact_capture)
      - Redis cache best-effort
      - Overlay response từ DB (ưu tiên) / raw_capture
    """
    from app.services.v1.handle_chatwoot.contact_capture import (
        cache_contact_capture_policy,
        contact_capture_from_inbox_payload,
        parse_contact_capture_policy,
        policy_to_public_dict,
    )
    from app.services.v1.handle_messaging_inbox_binding import (
        persist_binding_contact_capture,
    )

    inbox_obj = _inbox_obj_from_result(result)
    if inbox_obj is None:
        return

    website_token = _website_token_from_inbox_obj(inbox_obj)
    resolved_inbox_id = inbox_id
    if resolved_inbox_id is None and inbox_obj.get("id") is not None:
        try:
            resolved_inbox_id = int(inbox_obj["id"])
        except (TypeError, ValueError):
            resolved_inbox_id = None

    policy: dict[str, Any] | None = None
    if raw_capture is not None:
        policy = policy_to_public_dict(parse_contact_capture_policy(raw_capture))
        if resolved_inbox_id is not None:
            try:
                await persist_binding_contact_capture(
                    db,
                    tenant_id=tenant_id,
                    inbox_id=resolved_inbox_id,
                    website_token=website_token,
                    contact_capture=policy,
                )
            except Exception:
                logger.exception(
                    "Persist contact_capture DB thất bại tenant=%s inbox=%s",
                    tenant_id,
                    resolved_inbox_id,
                )
        await cache_contact_capture_policy(website_token=website_token, policy=policy)
    else:
        # Không gửi capture → overlay từ DB nếu có
        await _overlay_stored_contact_capture(
            db,
            tenant_id=tenant_id,
            inbox_id=resolved_inbox_id,
            result=result,
        )
        return

    # Overlay response với policy vừa lưu (không derive lệch từ CW)
    inbox_obj["contact_capture"] = policy
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    messaging = data.get("messaging") if isinstance(data, dict) else None
    if messaging is not inbox_obj and isinstance(messaging, dict):
        messaging["contact_capture"] = policy


async def _overlay_stored_contact_capture(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    result: Any,
    inbox_id: int | None = None,
) -> None:
    """Ưu tiên DB binding.contact_capture → Redis → giữ derive từ enrich."""
    from app.services.v1.handle_chatwoot.contact_capture import (
        get_cached_contact_capture_policy,
        parse_contact_capture_policy,
        policy_to_public_dict,
    )
    from app.services.v1.handle_messaging_inbox_binding import (
        contact_capture_from_binding,
        get_binding_by_tenant_inbox,
        get_binding_by_website_token,
    )

    inbox_obj = _inbox_obj_from_result(result)
    if inbox_obj is None:
        return

    website_token = _website_token_from_inbox_obj(inbox_obj)
    resolved_inbox_id = inbox_id
    if resolved_inbox_id is None and inbox_obj.get("id") is not None:
        try:
            resolved_inbox_id = int(inbox_obj["id"])
        except (TypeError, ValueError):
            resolved_inbox_id = None

    stored: dict[str, Any] | None = None
    try:
        binding = None
        if resolved_inbox_id is not None:
            binding = await get_binding_by_tenant_inbox(
                db, tenant_id, int(resolved_inbox_id)
            )
        if binding is None and website_token:
            binding = await get_binding_by_website_token(db, website_token)
        stored = contact_capture_from_binding(binding)
    except Exception:
        logger.exception("Overlay contact_capture từ DB thất bại")

    if stored is None:
        stored = await get_cached_contact_capture_policy(website_token)
    if stored is None:
        return

    public = policy_to_public_dict(parse_contact_capture_policy(stored))
    inbox_obj["contact_capture"] = public
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    messaging = data.get("messaging") if isinstance(data, dict) else None
    if messaging is not inbox_obj and isinstance(messaging, dict):
        messaging["contact_capture"] = public


async def upsert_livechat_contact(
    current_user: User,
    tenant_id: UUID,
    body: Any,
    db: AsyncSession,
):
    """
    PATCH Contact Chatwoot (name/email/phone) — hết “Khách truy cập”.
    Body: contact_id? | conversation_id?, name?, email?, phone?, source?
    """
    from app.services.v1.handle_chatwoot.contact_capture import (
        normalize_contact_payload,
        resolve_contact_id_for_conversation,
        upsert_chatwoot_contact,
    )
    from app.services.v1.handle_chatwoot.user_tokens import (
        resolve_agent_scoped_access_token,
    )

    denied = await _require_tenant_access(current_user, tenant_id, db)
    if denied is not None:
        return denied

    payload = body.model_dump(mode="json", exclude_unset=True) if hasattr(body, "model_dump") else dict(body or {})
    cleaned, errors = normalize_contact_payload(payload)
    if errors:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.BAD_REQUEST,
            "Thông tin liên hệ không hợp lệ",
            {"errors": errors},
        )
    if not cleaned:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.BAD_REQUEST,
            "Cần ít nhất một trong name / email / phone",
        )

    account_id, _ = await _resolve_account_id(db, tenant_id)
    if account_id is None:
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.NOT_FOUND,
            "Doanh nghiệp chưa được liên kết kênh trò chuyện.",
        )

    token, tok_err = await resolve_agent_scoped_access_token(db, current_user)
    if tok_err is not None:
        return tok_err

    contact_id = payload.get("contact_id")
    try:
        contact_id_int = int(contact_id) if contact_id is not None else None
    except (TypeError, ValueError):
        contact_id_int = None

    if contact_id_int is None:
        conv_id = payload.get("conversation_id")
        try:
            conv_id_int = int(conv_id) if conv_id is not None else None
        except (TypeError, ValueError):
            conv_id_int = None
        if conv_id_int is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.BAD_REQUEST,
                "Cần contact_id hoặc conversation_id",
            )
        contact_id_int = await resolve_contact_id_for_conversation(
            account_id=int(account_id),
            conversation_id=conv_id_int,
            access_token=token,
        )
        if contact_id_int is None:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.NOT_FOUND,
                "Không tìm thấy contact của hội thoại",
            )

    source = str(payload.get("source") or "agent_manual").strip()[:64] or "agent_manual"
    ok, status, data = await upsert_chatwoot_contact(
        account_id=int(account_id),
        contact_id=int(contact_id_int),
        name=cleaned.get("name"),
        email=cleaned.get("email"),
        phone=cleaned.get("phone"),
        additional_attributes={
            "omnihub_contact_capture": True,
            "omnihub_contact_source": source,
        },
        access_token=token,
    )
    if not ok:
        code = (
            ResponseStatusCode.SERVICE_UNAVAILABLE
            if status >= 500
            else ResponseStatusCode.BAD_REQUEST
        )
        if status in (401, 403, 404):
            code = ResponseStatusCode(status)
        return api_response(
            ResponseStatus.ERROR,
            code,
            "Cập nhật contact messaging thất bại",
            {"messaging_http_status": status, "messaging": data},
        )
    return api_response(
        ResponseStatus.SUCCESS,
        ResponseStatusCode.OK,
        "Đã cập nhật thông tin liên hệ trên messaging",
        {
            "tenant_id": str(tenant_id),
            "contact_id": int(contact_id_int),
            "contact": cleaned,
            "source": source,
            "messaging": data,
        },
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
        agent_scoped=True,
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
        agent_scoped=True,
    )


async def create_conversation_message(
    request: Request,
    current_user: User,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootApplicationJsonBody,
    db: AsyncSession,
):
    """POST messages — dùng token current_user (lưu 1 lần từ Platform)."""
    user_token = await ensure_user_chatwoot_api_token(db, current_user)
    if not user_token:
        return missing_token_api_response()
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
        access_token=user_token,
        agent_scoped=True,
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
        agent_scoped=True,
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
        agent_scoped=True,
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
        agent_scoped=True,
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
        agent_scoped=True,
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
        agent_scoped=True,
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
        agent_scoped=True,
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
        agent_scoped=True,
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
        agent_scoped=True,
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
        agent_scoped=True,
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
        agent_scoped=True,
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
        agent_scoped=True,
    )