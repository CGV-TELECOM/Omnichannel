from typing import Optional
from fastapi import APIRouter, Depends, Request, Query
from app.core.config.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.v1 import handle_permissions
from app.core.config.logging import log_user_action
from app.core.security.permissions import has_permission
from app.core.dependencies.dependencies import get_current_user_dependency
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.schemas.requests.permission import CreatePermissionTenantRequest, UpdatePermissionRequest
from app.db.models import User
from typing import List, Optional
from uuid import UUID

router = APIRouter(
    prefix="/permissions",
    tags=["Permissions"]
)

@router.get("/all")
async def get_permissions(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của quyền"),
    db: AsyncSession = Depends(get_db),
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm"),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_permissions"))
):
    return await handle_permissions.get_permissions(
        db=db,
        search=search,
        id=id,
        current_user=current_user
    )

@router.get("/{permission_id}")
# @log_user_action("view_permissions_by_id")
async def get_permission_by_id(
    permission_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_permissions"))
):
    return await handle_permissions.get_permission_by_id(permission_id, db, current_user)

@router.post("/create")
@log_user_action("create_permission")
async def create_permission(
    permissions: CreatePermissionTenantRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("create_permissions"))
):
    return await handle_permissions.create_tenant_permission(permissions, db, current_user)

@router.put("/{permission_id}")
@log_user_action("update_permission")
async def update_permission(
    permission_id: UUID,
    permission_data: UpdatePermissionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("edit_permissions"))
):
    return await handle_permissions.update_permission(permission_id, permission_data, db, current_user)

@router.delete("/{permission_id}")
@log_user_action("delete_permission")
async def delete_permission(
    permission_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("delete_permissions"))
):
    return await handle_permissions.delete_permission(permission_id, db, current_user)
