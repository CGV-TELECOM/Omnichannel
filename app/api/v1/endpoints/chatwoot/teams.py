from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.db.models import User
from app.schemas.requests.chatwoot import (
    ChatwootTeamCreateBody,
    ChatwootTeamUpdateBody,
    ChatwootTeamMembersBody,
)
from app.services.v1 import handle_chatwoot

router = APIRouter()


@router.get("/tenants/{tenant_id}/teams")
async def list_tenant_teams(
    request: Request,
    tenant_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """GET /api/v1/accounts/{account_id}/teams."""
    return await handle_chatwoot.list_teams(request, current_user, tenant_id, db)


@router.post("/tenants/{tenant_id}/teams")
@log_user_action("chatwootCreateTeam")
async def create_tenant_team(
    request: Request,
    tenant_id: UUID,
    body: ChatwootTeamCreateBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """POST /api/v1/accounts/{account_id}/teams."""
    return await handle_chatwoot.create_team(request, current_user, tenant_id, body, db)


@router.get("/tenants/{tenant_id}/teams/{team_id}")
async def get_tenant_team(
    request: Request,
    tenant_id: UUID,
    team_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """GET /api/v1/accounts/{account_id}/teams/{id}."""
    return await handle_chatwoot.get_team(request, current_user, tenant_id, team_id, db)


@router.patch("/tenants/{tenant_id}/teams/{team_id}")
@log_user_action("chatwootUpdateTeam")
async def update_tenant_team(
    request: Request,
    tenant_id: UUID,
    team_id: UUID,
    body: ChatwootTeamUpdateBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """PATCH /api/v1/accounts/{account_id}/teams/{id}."""
    return await handle_chatwoot.update_team(
        request, current_user, tenant_id, team_id, body, db
    )


@router.delete("/tenants/{tenant_id}/teams/{team_id}")
@log_user_action("chatwootDeleteTeam")
async def delete_tenant_team(
    request: Request,
    tenant_id: UUID,
    team_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """DELETE /api/v1/accounts/{account_id}/teams/{id}."""
    return await handle_chatwoot.delete_team(request, current_user, tenant_id, team_id, db)


@router.get("/tenants/{tenant_id}/teams/{team_id}/team_members")
async def list_tenant_team_members(
    request: Request,
    tenant_id: UUID,
    team_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """GET /api/v1/accounts/{account_id}/teams/{team_id}/team_members."""
    return await handle_chatwoot.list_team_members(request, current_user, tenant_id, team_id, db)


@router.post("/tenants/{tenant_id}/teams/{team_id}/team_members")
@log_user_action("chatwootAddTeamMembers")
async def add_tenant_team_members(
    request: Request,
    tenant_id: UUID,
    team_id: UUID,
    body: ChatwootTeamMembersBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """POST /api/v1/accounts/{account_id}/teams/{team_id}/team_members."""
    return await handle_chatwoot.add_team_members(
        request, current_user, tenant_id, team_id, body, db
    )


@router.delete("/tenants/{tenant_id}/teams/{team_id}/team_members")
@log_user_action("chatwootRemoveTeamMembers")
async def remove_tenant_team_members(
    request: Request,
    tenant_id: UUID,
    team_id: UUID,
    body: ChatwootTeamMembersBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """DELETE /api/v1/accounts/{account_id}/teams/{team_id}/team_members."""
    return await handle_chatwoot.remove_team_members(
        request, current_user, tenant_id, team_id, body, db
    )


@router.patch("/tenants/{tenant_id}/teams/{team_id}/team_members")
@log_user_action("chatwootUpdateTeamMembers")
async def update_tenant_team_members(
    request: Request,
    tenant_id: UUID,
    team_id: UUID,
    body: ChatwootTeamMembersBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """PATCH /api/v1/accounts/{account_id}/teams/{team_id}/team_members."""
    return await handle_chatwoot.update_team_members(
        request, current_user, tenant_id, team_id, body, db
    )
