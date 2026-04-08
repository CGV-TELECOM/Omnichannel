from fastapi import APIRouter, Depends, Request, Query
from app.core.config.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.v1 import handle_role_permission
from app.core.config.logging import log_user_action
from app.core.security.permissions import has_permission
from app.schemas.requests.role_permission import AssignPermissionsRequest, RemovePermissionRequest
from app.utils.helpers import isCheckMaxLevel
from app.core.dependencies.dependencies import get_current_user_dependency
from app.db.models import User
from typing import Optional, Union
from uuid import UUID


router = APIRouter(
    prefix="/role-permission",
    tags=["Role Permission"],
)

@router.get("/{role_id}")
async def get_role_permissions(
    role_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_role_permissions_by_role_id"))
):

    return await handle_role_permission.get_role_permissions(role_id, db, current_user)

@router.post("/{role_id}/assign")
@log_user_action("assign_permissions_to_role")
async def assign_permissions_to_role(
    role_id: UUID,
    permissions: AssignPermissionsRequest,
    request: Request,  
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    _ = Depends(has_permission("assign_permissions_to_role"))
):
    return await handle_role_permission.assign_permissions_to_role(
        role_id=role_id,
        current_user=current_user,
        permission_ids=permissions.permission_ids,
        tenant_id=permissions.tenant_id, 
        db=db
    )

@router.delete("/{role_id}/permissions/{permission_id}")
@log_user_action("remove_permission_from_role")
async def remove_permission_from_role(
    role_id: UUID,
    permission_id: UUID,
    request: Request,
    tenant_id: Optional[UUID] = Query(None),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    _ = Depends(has_permission("delete_permission_from_role"))
):
    """
    Remove a specific permission from a role
    """
    return await handle_role_permission.remove_permission_from_role(
        role_id=role_id,
        current_user=current_user,
        permission_id=permission_id,
        tenant_id=tenant_id,
        db=db
    )



