from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.security.permissions import has_permission
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
    _=Depends(has_permission("view_messaging_teams")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.list_teams(request, current_user, tenant_id, db)


@router.post("/tenants/{tenant_id}/teams")
@log_user_action("chatwootCreateTeam")
async def create_tenant_team(
    request: Request,
    tenant_id: UUID,
    body: ChatwootTeamCreateBody,
    _=Depends(has_permission("create_messaging_team")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.create_team(request, current_user, tenant_id, body, db)


@router.get("/tenants/{tenant_id}/teams/{team_id}")
async def get_tenant_team(
    request: Request,
    tenant_id: UUID,
    team_id: UUID,
    _=Depends(has_permission("view_messaging_teams")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.get_team(request, current_user, tenant_id, team_id, db)


@router.patch("/tenants/{tenant_id}/teams/{team_id}")
@log_user_action("chatwootUpdateTeam")
async def update_tenant_team(
    request: Request,
    tenant_id: UUID,
    team_id: UUID,
    body: ChatwootTeamUpdateBody,
    _=Depends(has_permission("edit_messaging_team")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.update_team(
        request, current_user, tenant_id, team_id, body, db
    )


@router.delete("/tenants/{tenant_id}/teams/{team_id}")
@log_user_action("chatwootDeleteTeam")
async def delete_tenant_team(
    request: Request,
    tenant_id: UUID,
    team_id: UUID,
    _=Depends(has_permission("delete_messaging_team")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.delete_team(request, current_user, tenant_id, team_id, db)


@router.get("/tenants/{tenant_id}/teams/{team_id}/team_members")
async def list_tenant_team_members(
    request: Request,
    tenant_id: UUID,
    team_id: UUID,
    _=Depends(has_permission("view_messaging_teams")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.list_team_members(request, current_user, tenant_id, team_id, db)


@router.post("/tenants/{tenant_id}/teams/{team_id}/team_members")
@log_user_action("chatwootAddTeamMembers")
async def add_tenant_team_members(
    request: Request,
    tenant_id: UUID,
    team_id: UUID,
    body: ChatwootTeamMembersBody,
    _=Depends(has_permission("manage_messaging_team_members")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
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
    _=Depends(has_permission("manage_messaging_team_members")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
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
    _=Depends(has_permission("manage_messaging_team_members")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.update_team_members(
        request, current_user, tenant_id, team_id, body, db
    )
