from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.security.permissions import has_permission
from app.db.models import User
from app.schemas.requests.chatwoot import (
    ChatwootApplicationJsonBody,
    ChatwootConversationAssignBody,
    ChatwootConversationCustomAttributesBody,
    ChatwootConversationLabelsMutationBody,
    ChatwootConversationToggleStatusBody,
    ChatwootConversationTypingBody,
    ConversationFilterRequest,
)
from app.services.v1 import handle_chatwoot

router = APIRouter()


@router.get("/tenants/{tenant_id}/inboxes")
async def list_tenant_inboxes(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_messaging_inboxes")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.list_inboxes(request, current_user, tenant_id, db)


@router.post("/tenants/{tenant_id}/inboxes")
@log_user_action("chatwootCreateInbox")
async def create_tenant_inbox(
    request: Request,
    tenant_id: UUID,
    body: ChatwootApplicationJsonBody,
    _=Depends(has_permission("create_messaging_inbox")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.create_inbox(request, current_user, tenant_id, body, db)


@router.get("/tenants/{tenant_id}/inboxes/{inbox_id}")
async def get_tenant_inbox(
    request: Request,
    tenant_id: UUID,
    inbox_id: int,
    _=Depends(has_permission("view_messaging_inboxes")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.get_inbox(request, current_user, tenant_id, inbox_id, db)


@router.patch("/tenants/{tenant_id}/inboxes/{inbox_id}")
@log_user_action("chatwootUpdateInbox")
async def update_tenant_inbox(
    request: Request,
    tenant_id: UUID,
    inbox_id: int,
    body: ChatwootApplicationJsonBody,
    _=Depends(has_permission("edit_messaging_inbox")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.update_inbox(
        request, current_user, tenant_id, inbox_id, body, db
    )


@router.get("/tenants/{tenant_id}/labels")
async def list_tenant_labels(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_messaging_labels")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.list_labels(request, current_user, tenant_id, db)


@router.post("/tenants/{tenant_id}/labels")
@log_user_action("chatwootCreateLabel")
async def create_tenant_label(
    request: Request,
    tenant_id: UUID,
    body: ChatwootApplicationJsonBody,
    _=Depends(has_permission("create_messaging_label")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.create_label(request, current_user, tenant_id, body, db)


@router.delete("/tenants/{tenant_id}/labels/{label}")
@log_user_action("chatwootDeleteLabel")
async def delete_tenant_label(
    request: Request,
    tenant_id: UUID,
    label: str,
    _=Depends(has_permission("delete_messaging_label")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.delete_label(request, current_user, tenant_id, label, db)


@router.get("/tenants/{tenant_id}/conversations")
async def list_tenant_conversations(
    request: Request,
    tenant_id: UUID,
    status: str | None = Query(None, description="Trạng thái: open, resolved, pending, snoozed, all"),
    assignee_type: str | None = Query(None, description="Loại assignee: me, unassigned, all, assigned"),
    team_id: UUID | None = Query(None, description="UUID team trong contact-center (bảng map)"),
    inbox_id: int | None = Query(None, description="ID inbox"),
    page: int | None = Query(None, description="Số trang (mặc định 1)"),
    sort_by: str | None = Query(None, description="Sắp xếp: last_activity_at_desc, last_activity_at_asc, created_at_desc, etc."),
    q: str | None = Query(None, description="Từ khóa tìm kiếm"),
    labels: str | None = Query(None, description="Nhãn conversation"),
    _=Depends(has_permission("view_messaging_conversations")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.list_conversations(
        request, current_user, tenant_id, db
    )


@router.delete("/tenants/{tenant_id}/conversations/{conversation_id}")
async def delete_tenant_conversation(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    _=Depends(has_permission("delete_messaging_conversation")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.delete_conversation(
        request, current_user, tenant_id, conversation_id, db
    )


@router.post("/tenants/{tenant_id}/conversations/filter")
async def filter_tenant_conversations(
    request: Request,
    tenant_id: UUID,
    body: ConversationFilterRequest,
    _=Depends(has_permission("view_messaging_conversations")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.filter_conversations(
        request,
        current_user,
        tenant_id,
        body,
        db,
    )


@router.post("/tenants/{tenant_id}/conversations")
@log_user_action("chatwootCreateConversation")
async def create_tenant_conversation(
    request: Request,
    tenant_id: UUID,
    body: ChatwootApplicationJsonBody,
    _=Depends(has_permission("create_messaging_conversation")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.create_conversation(
        request, current_user, tenant_id, body, db
    )


@router.get("/tenants/{tenant_id}/conversations/{conversation_id}")
async def get_tenant_conversation(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    _=Depends(has_permission("view_messaging_conversations")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.get_conversation(
        request, current_user, tenant_id, conversation_id, db
    )


@router.patch("/tenants/{tenant_id}/conversations/{conversation_id}")
@log_user_action("chatwootUpdateConversation")
async def update_tenant_conversation(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootApplicationJsonBody,
    _=Depends(has_permission("edit_messaging_conversation")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.update_conversation(
        request, current_user, tenant_id, conversation_id, body, db
    )


@router.get("/tenants/{tenant_id}/conversations/{conversation_id}/messages")
async def list_tenant_conversation_messages(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    _=Depends(has_permission("view_messaging_conversations")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.list_conversation_messages(
        request, current_user, tenant_id, conversation_id, db
    )


@router.post("/tenants/{tenant_id}/conversations/{conversation_id}/messages")
@log_user_action("chatwootCreateMessage")
async def create_tenant_conversation_message(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootApplicationJsonBody,
    _=Depends(has_permission("send_messaging_message")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.create_conversation_message(
        request, current_user, tenant_id, conversation_id, body, db
    )


@router.delete("/tenants/{tenant_id}/conversations/{conversation_id}/messages/{message_id}")
@log_user_action("chatwootDeleteMessage")
async def delete_tenant_conversation_message(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    message_id: int,
    _=Depends(has_permission("delete_messaging_message")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.delete_conversation_message(
        request, current_user, tenant_id, conversation_id, message_id, db
    )


@router.post("/tenants/{tenant_id}/conversations/{conversation_id}/toggle_status")
@log_user_action("chatwootToggleConversationStatus")
async def toggle_tenant_conversation_status(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootConversationToggleStatusBody,
    _=Depends(has_permission("edit_messaging_conversation")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.toggle_conversation_status(
        request, current_user, tenant_id, conversation_id, body, db
    )


@router.get("/tenants/{tenant_id}/conversations/{conversation_id}/labels")
async def get_tenant_conversation_labels(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    _=Depends(has_permission("view_messaging_conversations")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.get_conversation_labels(
        request, current_user, tenant_id, conversation_id, db
    )


@router.post("/tenants/{tenant_id}/conversations/{conversation_id}/labels")
@log_user_action("chatwootSetConversationLabels")
async def set_tenant_conversation_labels(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootConversationLabelsMutationBody,
    _=Depends(has_permission("edit_messaging_conversation")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.set_conversation_labels(
        request, current_user, tenant_id, conversation_id, body, db
    )


@router.post("/tenants/{tenant_id}/conversations/{conversation_id}/toggle_typing_status")
@log_user_action("chatwootToggleConversationTyping")
async def toggle_tenant_conversation_typing(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootConversationTypingBody,
    _=Depends(has_permission("edit_messaging_conversation")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.toggle_conversation_typing(
        request, current_user, tenant_id, conversation_id, body, db
    )


@router.post("/tenants/{tenant_id}/conversations/{conversation_id}/custom_attributes")
@log_user_action("chatwootUpdateConversationCustomAttributes")
async def update_tenant_conversation_custom_attributes(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootConversationCustomAttributesBody,
    _=Depends(has_permission("edit_messaging_conversation")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.update_conversation_custom_attributes(
        request, current_user, tenant_id, conversation_id, body, db
    )


@router.post("/tenants/{tenant_id}/conversations/{conversation_id}/assignments")
@log_user_action("chatwootAssignConversation")
async def assign_tenant_conversation(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    body: ChatwootConversationAssignBody,
    _=Depends(has_permission("assign_messaging_conversation")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.assign_conversation(
        current_user, tenant_id, conversation_id, body, db
    )


@router.get("/tenants/{tenant_id}/conversations/{conversation_id}/attachments")
async def list_tenant_conversation_attachments(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    _=Depends(has_permission("view_messaging_conversations")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.get_attachment(
        request, current_user, tenant_id, conversation_id, db
    )


@router.post("/tenants/{tenant_id}/conversations/{conversation_id}/update_last_seen")
async def update_tenant_conversation_last_seen(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    _=Depends(has_permission("edit_messaging_conversation")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.update_last_seen(
        request,
        current_user,
        tenant_id,
        conversation_id,
        db,
    )
