from uuid import UUID

from fastapi import APIRouter, Depends, Request
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
    ConversationFilter,
    ConversationFilterRequest,
)
from app.services.v1 import handle_chatwoot

router = APIRouter()


@router.get("/tenants/{tenant_id}/inboxes")
async def list_tenant_inboxes(
    request: Request,
    tenant_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """GET /api/v1/accounts/{account_id}/inboxes — listAllInboxes."""
    return await handle_chatwoot.list_inboxes(request, current_user, tenant_id, db)


@router.post("/tenants/{tenant_id}/inboxes")
@log_user_action("chatwootCreateInbox")
async def create_tenant_inbox(
    request: Request,
    tenant_id: UUID,
    body: ChatwootApplicationJsonBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """POST /api/v1/accounts/{account_id}/inboxes — body giống Chatwoot `inbox_create_payload`."""
    return await handle_chatwoot.create_inbox(request, current_user, tenant_id, body, db)


@router.get("/tenants/{tenant_id}/inboxes/{inbox_id}")
async def get_tenant_inbox(
    request: Request,
    tenant_id: UUID,
    inbox_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """GET /api/v1/accounts/{account_id}/inboxes/{id}."""
    return await handle_chatwoot.get_inbox(request, current_user, tenant_id, inbox_id, db)


@router.patch("/tenants/{tenant_id}/inboxes/{inbox_id}")
@log_user_action("chatwootUpdateInbox")
async def update_tenant_inbox(
    request: Request,
    tenant_id: UUID,
    inbox_id: int,
    body: ChatwootApplicationJsonBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """PATCH /api/v1/accounts/{account_id}/inboxes/{id}."""
    return await handle_chatwoot.update_inbox(
        request, current_user, tenant_id, inbox_id, body, db
    )


@router.get("/tenants/{tenant_id}/labels")
async def list_tenant_labels(
    request: Request,
    tenant_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """GET /api/v1/accounts/{account_id}/labels."""
    return await handle_chatwoot.list_labels(request, current_user, tenant_id, db)


@router.post("/tenants/{tenant_id}/labels")
@log_user_action("chatwootCreateLabel")
async def create_tenant_label(
    request: Request,
    tenant_id: UUID,
    body: ChatwootApplicationJsonBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """POST /api/v1/accounts/{account_id}/labels."""
    return await handle_chatwoot.create_label(request, current_user, tenant_id, body, db)


@router.delete("/tenants/{tenant_id}/labels/{label}")
@log_user_action("chatwootDeleteLabel")
async def delete_tenant_label(
    request: Request,
    tenant_id: UUID,
    label: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """DELETE /api/v1/accounts/{account_id}/labels/{label_title}."""
    return await handle_chatwoot.delete_label(request, current_user, tenant_id, label, db)


@router.get("/tenants/{tenant_id}/conversations")
async def list_tenant_conversations(
    request: Request,
    tenant_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """GET /api/v1/accounts/{account_id}/conversations — query whitelist trong handler."""
    return await handle_chatwoot.list_conversations(
        request, current_user, tenant_id, db
    )

@router.delete("/tenants/{tenant_id}/conversations/{conversation_id}")
async def delete_tenant_conversation(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """DELETE /api/v1/accounts/{account_id}/conversations/{conversation_id}."""
    return await handle_chatwoot.delete_conversation(
        request, current_user, tenant_id, conversation_id, db
)

@router.post("/tenants/{tenant_id}/conversations/filter")
async def filter_tenant_conversations(
    request: Request,
    tenant_id: UUID,
    body: ConversationFilterRequest,
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """POST /api/v1/accounts/{account_id}/conversations — `source_id` + `inbox_id` theo doc Chatwoot."""
    return await handle_chatwoot.create_conversation(
        request, current_user, tenant_id, body, db
    )


@router.get("/tenants/{tenant_id}/conversations/{conversation_id}")
async def get_tenant_conversation(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """PATCH /api/v1/accounts/{account_id}/conversations/{conversation_id}."""
    return await handle_chatwoot.update_conversation(
        request, current_user, tenant_id, conversation_id, body, db
    )


@router.get("/tenants/{tenant_id}/conversations/{conversation_id}/messages")
async def list_tenant_conversation_messages(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """POST /api/v1/accounts/{account_id}/conversations/{conversation_id}/messages — gửi tin (body giống Chatwoot)."""
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """DELETE .../messages/{message_id} — Chatwoot Application API."""
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """POST .../toggle_status."""
    return await handle_chatwoot.toggle_conversation_status(
        request, current_user, tenant_id, conversation_id, body, db
    )


@router.get("/tenants/{tenant_id}/conversations/{conversation_id}/labels")
async def get_tenant_conversation_labels(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """GET .../labels."""
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """POST .../labels — ghi đè toàn bộ label."""
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """POST .../toggle_typing_status."""
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """POST .../custom_attributes."""
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """GET .../attachments."""
    return await handle_chatwoot.get_attachment(
        request, current_user, tenant_id, conversation_id, db
    )

@router.post("/tenants/{tenant_id}/conversations/{conversation_id}/update_last_seen")
async def update_tenant_conversation_last_seen(
    request: Request,
    tenant_id: UUID,
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """POST .../update_last_seen."""
    return await handle_chatwoot.update_last_seen(
        request,
        current_user,
        tenant_id,
        conversation_id,
        db,
    )