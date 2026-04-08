from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.security.permissions import has_permission
from app.db.models import User
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
from app.services.v1 import handle_chatwoot
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db

router = APIRouter(prefix="/chatwoot", tags=["Chatwoot"])


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


@router.post("/users")
@log_user_action("chatwootCreateUser")
async def create_chatwoot_user(
    request: Request,
    body: ChatwootUserCreateBody,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.create_user(request, current_user, body, db)


@router.get("/users/{user_id}")
async def get_chatwoot_user(
    request: Request,
    user_id: UUID,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.get_user(request, current_user, user_id, db)


@router.patch("/users/{user_id}")
@log_user_action("chatwootUpdateUser")
async def update_chatwoot_user(
    request: Request,
    user_id: UUID,
    body: ChatwootUserUpdateBody,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.update_user(request, current_user, user_id, body, db)


@router.delete("/users/{user_id}")
@log_user_action("chatwootDeleteUser")
async def delete_chatwoot_user(
    request: Request,
    user_id: UUID,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.delete_user(request, current_user, user_id, db)


@router.get("/users/{user_id}/sso-link")
async def get_chatwoot_user_sso_link(
    request: Request,
    user_id: UUID,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.get_user_sso_link(request, current_user, user_id, db)


@router.get("/agent-bots")
async def list_all_chatwoot_agent_bots(
    request: Request,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """Platform API: danh sách mọi AgentBot trên instance Chatwoot (xem developers.chatwoot.com AgentBots)."""
    return await handle_chatwoot.list_all_agent_bots(request, current_user, db)


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


@router.get("/tenants/{tenant_id}/agent-bots")
async def list_tenant_chatwoot_agent_bots(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.list_tenant_agent_bots(
        request, current_user, tenant_id, db
    )


@router.post("/tenants/{tenant_id}/agent-bots")
@log_user_action("chatwootCreateAgentBot")
async def create_chatwoot_agent_bot(
    request: Request,
    tenant_id: UUID,
    body: ChatwootAgentBotCreateBody,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.create_agent_bot(
        request, current_user, tenant_id, body, db
    )


@router.get("/tenants/{tenant_id}/agent-bots/{bot_id}")
async def get_chatwoot_agent_bot(
    request: Request,
    tenant_id: UUID,
    bot_id: UUID,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.get_agent_bot(
        request, current_user, tenant_id, bot_id, db
    )


@router.patch("/tenants/{tenant_id}/agent-bots/{bot_id}")
@log_user_action("chatwootUpdateAgentBot")
async def update_chatwoot_agent_bot(
    request: Request,
    tenant_id: UUID,
    bot_id: UUID,
    body: ChatwootAgentBotUpdateBody,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.update_agent_bot(
        request, current_user, tenant_id, bot_id, body, db
    )


@router.delete("/tenants/{tenant_id}/agent-bots/{bot_id}")
@log_user_action("chatwootDeleteAgentBot")
async def delete_chatwoot_agent_bot(
    request: Request,
    tenant_id: UUID,
    bot_id: UUID,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.delete_agent_bot(
        request, current_user, tenant_id, bot_id, db
    )


@router.get("/tenants/{tenant_id}/agents")
async def list_chatwoot_agents(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.list_agents(request, current_user, tenant_id, db)


@router.post("/tenants/{tenant_id}/agents")
@log_user_action("chatwootCreateAgent")
async def create_chatwoot_agent(
    request: Request,
    tenant_id: UUID,
    body: ChatwootAgentCreateBody,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.create_agent(
        request, current_user, tenant_id, body, db
    )


@router.patch("/tenants/{tenant_id}/agents/{agent_id}")
@log_user_action("chatwootUpdateAgent")
async def update_chatwoot_agent(
    request: Request,
    tenant_id: UUID,
    agent_id: UUID,
    body: ChatwootAgentUpdateBody,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.update_agent(
        request, current_user, tenant_id, agent_id, body, db
    )


@router.delete("/tenants/{tenant_id}/agents/{agent_id}")
@log_user_action("chatwootDeleteAgent")
async def delete_chatwoot_agent(
    request: Request,
    tenant_id: UUID,
    agent_id: UUID,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.delete_agent(
        request, current_user, tenant_id, agent_id, db
    )
