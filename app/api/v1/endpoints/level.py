from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from app.services.v1 import handle_level
from app.core.config.logging import log_user_action
from app.core.security.permissions import has_permission
from app.core.config.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.requests.level import CreateLevelRequest, UpdateLevelRequest
from app.db.models import User
from app.core.dependencies.dependencies import get_current_user_dependency
from uuid import UUID

router = APIRouter(prefix="/levels", tags=["Level"])

@router.get("")
async def get_levels(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của level"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Number of items per page"),
    db: AsyncSession = Depends(get_db),
    _ = Depends(has_permission("view_levels")),
    current_user: User = Depends(get_current_user_dependency)
):
    return await handle_level.get_levels(id, page, page_size, db, current_user)

@router.get("/{level_id}")
async def get_level(
    request: Request,
    level_id: UUID,
    db: AsyncSession = Depends(get_db),
    _ = Depends(has_permission("view_level_by_id")),
    current_user: User = Depends(get_current_user_dependency)
):
    return await handle_level.get_level_by_id(level_id, db, current_user)

# @router.post("")
# @log_user_action("create_level")
# async def create_level(
#     request: Request,
#     level_data: CreateLevelRequest,
#     db: AsyncSession = Depends(get_db),
#     _ = Depends(has_permission("create_level"))
# ):
#     return await handle_level.create_level(level_data, db)

# @router.put("/{level_id}")
# @log_user_action("edit_level")
# async def update_level(
#     request: Request,
#     level_id: int,
#     level_data: UpdateLevelRequest,
#     db: AsyncSession = Depends(get_db),
#     _ = Depends(has_permission("edit_level"))
# ):
#     return await handle_level.update_level(level_id, level_data, db)

# @router.delete("/{level_id}")
# @log_user_action("delete_level")
# async def delete_level(
#     request: Request,
#     level_id: int,
#     db: AsyncSession = Depends(get_db),
#     _ = Depends(has_permission("delete_level"))
# ):
#     return await handle_level.delete_level(level_id, db)