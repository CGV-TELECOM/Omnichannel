from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.security.permissions import has_permission
from app.db.models import User
from app.schemas.requests.chatwoot import ChatwootAgentCreateBody, ChatwootAgentUpdateBody
from app.services.v1 import handle_chatwoot

router = APIRouter()


@router.get("/tenants/{tenant_id}/agents")
async def list_chatwoot_agents(
    request: Request,
    tenant_id: UUID,
    _=Depends(has_permission("view_messaging_agents")),
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
    _=Depends(has_permission("create_messaging_agent")),
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
    _=Depends(has_permission("edit_messaging_agent")),
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
    _=Depends(has_permission("delete_messaging_agent")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.delete_agent(
        request, current_user, tenant_id, agent_id, db
    )
