from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.security.permissions import has_permission
from app.db.models import User
from app.schemas.requests.chatwoot import ChatwootUserCreateBody, ChatwootUserUpdateBody
from app.services.v1 import handle_chatwoot

router = APIRouter()


@router.post("/users/{user_id}")
@log_user_action("chatwootCreateUser")
async def create_chatwoot_user(
    request: Request,
    user_id: UUID,
    body: ChatwootUserCreateBody,
    _=Depends(has_permission("view_roles")),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_chatwoot.create_user(request, current_user, user_id, body, db)


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
