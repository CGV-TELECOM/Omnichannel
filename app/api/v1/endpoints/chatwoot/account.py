from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.security.permissions import has_permission
from app.db.models import User
from app.schemas.requests.chatwoot import ChatwootProvisionAccountBody, ChatwootUpdateAccountBody, ChatwootBulkActionLabelsBody, ChatwootCustomFiltersBody, ChatwootActionAgentInboxesBody
from app.services.v1 import handle_chatwoot

router = APIRouter()


@router.post("/accounts")
@log_user_action("chatwootProvisionAccount")
async def provision_chatwoot_account(
    request: Request,
    body: ChatwootProvisionAccountBody,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.provision_account(request, current_user, body, db)


@router.get("/tenants/{tenant_id}/account")
async def get_chatwoot_account(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.get_account(request, current_user, tenant_id, db)


@router.patch("/tenants/{tenant_id}/account")
@log_user_action("chatwootUpdateAccount")
async def update_chatwoot_account(
    request: Request,
    tenant_id: UUID,
    body: ChatwootUpdateAccountBody,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.update_account(
        request, current_user, tenant_id, body, db
    )


@router.delete("/tenants/{tenant_id}/account")
@log_user_action("chatwootDeleteAccount")
async def delete_chatwoot_account(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.delete_account(request, current_user, tenant_id, db)


@router.post("/tenants/{tenant_id}/integration-account-user")
@log_user_action("chatwootSyncIntegrationAccountUser")
async def sync_chatwoot_integration_account_user(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.sync_integration_account_user(
        request, current_user, tenant_id, db
    )

@router.post("/accounts/{tenant_id}/bulk_actions") 
@log_user_action("chatwootBulkActionLabels")
async def bulk_action_labels(
    request: Request,
    tenant_id: UUID,
    body: ChatwootBulkActionLabelsBody,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.bulk_action_account(
        request, current_user, tenant_id, body, db
    )

@router.post("/accounts/{tenant_id}/inbox_members") 
@log_user_action("chatwootActionAgentInboxes")
async def action_agent_inboxes(
    request: Request,
    tenant_id: UUID,
    body: ChatwootActionAgentInboxesBody,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.add_new_agent_inboxes(
        request, current_user, tenant_id, body, db
    )

@router.patch("/accounts/{tenant_id}/inbox_members") 
@log_user_action("chatwootPatchAgentInboxes")
async def patch_agent_inboxes(
    request: Request,
    tenant_id: UUID,
    body: ChatwootActionAgentInboxesBody,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.patch_new_agent_inboxes(
        request, current_user, tenant_id, body, db
    )


@router.get("/accounts/{tenant_id}/custom_filters")
@log_user_action("getChatwootCustomFilters")
async def get_custom_filters_route(
    request: Request,
    tenant_id: UUID,
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    return await handle_chatwoot.get_custom_filters(
        request=request,
        current_user=current_user,
        tenant_id=tenant_id,
        db=db,
    )

@router.post("/accounts/{tenant_id}/custom_filters")
@log_user_action("chatwootCustomFilters")
async def custom_filters(
    request: Request,
    tenant_id: UUID,
    body: ChatwootCustomFiltersBody,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.custom_filters(
        request, current_user, tenant_id, body, db
    )

@router.patch(
    "/accounts/{tenant_id}/custom_filters/{filter_id}"
)
@log_user_action("chatwootUpdateCustomFilter")
async def update_custom_filter_route(
    request: Request,
    tenant_id: UUID,
    filter_id: int,
    body: ChatwootCustomFiltersBody,
    _=Depends(has_permission("view_roles")),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    return await handle_chatwoot.update_custom_filter(
        request=request,
        current_user=current_user,
        tenant_id=tenant_id,
        filter_id=filter_id,
        body=body,
        db=db,
    )


@router.delete(
    "/accounts/{tenant_id}/custom_filters/{filter_id}"
)
@log_user_action("chatwootDeleteCustomFilter")
async def delete_custom_filter_route(
    request: Request,
    tenant_id: UUID,
    filter_id: int,
    _=Depends(has_permission("view_roles")),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
):
    return await handle_chatwoot.delete_custom_filter(
        request=request,
        current_user=current_user,
        tenant_id=tenant_id,
        filter_id=filter_id,
        db=db,
    )