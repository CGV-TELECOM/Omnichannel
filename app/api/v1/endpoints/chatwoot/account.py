from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.security.permissions import has_permission
from app.db.models import User
from app.schemas.requests.chatwoot import ChatwootProvisionAccountBody, ChatwootUpdateAccountBody, ChatwootBulkActionLabelsBody
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
