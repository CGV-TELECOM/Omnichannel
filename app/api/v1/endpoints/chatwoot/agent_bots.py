from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.security.permissions import has_permission
from app.db.models import User
from app.schemas.requests.chatwoot import (
    ChatwootAgentBotCreateBody,
    ChatwootAgentBotUpdateBody,
    ChatwootApplicationJsonBody,
)
from app.services.v1 import handle_chatwoot

router = APIRouter()


@router.get("/agent-bots")
async def list_all_chatwoot_agent_bots(
    request: Request,
    _=Depends(has_permission("view_messaging_agent_bots")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """Platform API: danh sách mọi AgentBot trên instance messaging."""
    return await handle_chatwoot.list_all_agent_bots(request, current_user, db)


@router.get("/tenants/{tenant_id}/agent-bots")
async def list_tenant_chatwoot_agent_bots(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_messaging_agent_bots")),
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
    _=Depends(has_permission("create_messaging_agent_bot")),
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
    _=Depends(has_permission("view_messaging_agent_bots")),
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
    _=Depends(has_permission("edit_messaging_agent_bot")),
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
    _=Depends(has_permission("delete_messaging_agent_bot")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.delete_agent_bot(
        request, current_user, tenant_id, bot_id, db
    )


@router.get("/tenants/{tenant_id}/account-agent-bots")
async def list_tenant_account_agent_bots(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_messaging_agent_bots")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.list_account_agent_bots(
        request, current_user, tenant_id, db
    )


@router.post("/tenants/{tenant_id}/account-agent-bots")
@log_user_action("chatwootCreateAccountAgentBot")
async def create_tenant_account_agent_bot(
    request: Request,
    tenant_id: UUID,
    body: ChatwootApplicationJsonBody,
    _=Depends(has_permission("create_messaging_agent_bot")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.create_account_agent_bot(
        request, current_user, tenant_id, body, db
    )


@router.get("/tenants/{tenant_id}/account-agent-bots/{agent_bot_id}")
async def get_tenant_account_agent_bot(
    request: Request,
    tenant_id: UUID,
    agent_bot_id: int,
    _=Depends(has_permission("view_messaging_agent_bots")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.get_account_agent_bot(
        request, current_user, tenant_id, agent_bot_id, db
    )


@router.patch("/tenants/{tenant_id}/account-agent-bots/{agent_bot_id}")
@log_user_action("chatwootUpdateAccountAgentBot")
async def update_tenant_account_agent_bot(
    request: Request,
    tenant_id: UUID,
    agent_bot_id: int,
    body: ChatwootApplicationJsonBody,
    _=Depends(has_permission("edit_messaging_agent_bot")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.update_account_agent_bot(
        request, current_user, tenant_id, agent_bot_id, body, db
    )


@router.delete("/tenants/{tenant_id}/account-agent-bots/{agent_bot_id}")
@log_user_action("chatwootDeleteAccountAgentBot")
async def delete_tenant_account_agent_bot(
    request: Request,
    tenant_id: UUID,
    agent_bot_id: int,
    _=Depends(has_permission("delete_messaging_agent_bot")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.delete_account_agent_bot(
        request, current_user, tenant_id, agent_bot_id, db
    )
