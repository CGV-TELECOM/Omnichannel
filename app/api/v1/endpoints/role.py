from fastapi import APIRouter, Depends, Request, Query
from typing import Optional
from app.core.config.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.v1 import handle_role
from app.db.models import User        
from app.core.config.logging import log_user_action
from app.core.security.permissions import has_permission
from app.schemas.requests.role import CreateRoleRequest, UpdateRoleRequest
from app.core.dependencies.dependencies import get_current_user_dependency
from uuid import UUID

router = APIRouter(
    prefix="/roles",
    tags=["Role"]
)

@router.get("")
async def get_roles(request: Request, 
                    id: Optional[UUID] = Query(None, description="ID của vai trò"),
                    page: int = Query(1, ge=1, description="Số trang"),
                    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
                    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm"),
                    sort_by: Optional[str] = Query(None, description="Trường sắp xếp"),
                    sort_order: str = Query("asc", description="Thứ tự sắp xếp (asc/desc)"),
                    tenant_id: Optional[UUID] = Query(
                        None,
                        description="Chỉ platform admin: xem role của tenant đích. Tenant khác bị bỏ qua.",
                    ),
                    _ = Depends(has_permission("view_roles")),
                    current_user: User = Depends(get_current_user_dependency),
                    db: AsyncSession = Depends(get_db)):
    return await handle_role.get_roles(
        id, page, page_size, search, sort_by, sort_order, db, current_user, tenant_id
    )


# @router.get("/{role_id}")
# # @log_user_action("get_role_by_id")
# async def get_role_by_id(request: Request,
#                         role_id: int,
#                         db: AsyncSession = Depends(get_db),
#                         _ = Depends(has_permission("view_roles_by_id"))):
#     return await handle_role.get_role_by_id(role_id, db)

@router.post("")
@log_user_action("create_role")
async def create_role(request: Request,
                     role_data: CreateRoleRequest,
                     db: AsyncSession = Depends(get_db),
                     current_user: User = Depends(get_current_user_dependency),
                     _ = Depends(has_permission("create_roles"))):
    return await handle_role.create_role(role_data, current_user, db)


@router.put("/{role_id}")
@log_user_action("update_role")
async def update_role(request: Request,
                     role_id: UUID,
                     role_data: UpdateRoleRequest,
                     db: AsyncSession = Depends(get_db),
                     current_user: User = Depends(get_current_user_dependency),
                     _ = Depends(has_permission("edit_roles"))):
    return await handle_role.update_role(role_id, role_data, current_user, db)


@router.delete("/{role_id}")
@log_user_action("delete_role")
async def delete_role(request: Request,
                     role_id: UUID,
                     db: AsyncSession = Depends(get_db),
                     current_user: User = Depends(get_current_user_dependency),
                     _ = Depends(has_permission("delete_roles"))):
    return await handle_role.delete_role(role_id, current_user, db)