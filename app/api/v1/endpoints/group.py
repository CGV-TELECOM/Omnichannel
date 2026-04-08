from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.core.config.database import get_db
from app.schemas.requests.group import GroupCreate, GroupUpdate
from app.db.models import Group, User
from app.core.security.permissions import has_permission
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.services.v1 import handle_group
from uuid import UUID

router = APIRouter(
    prefix="/groups",
    tags=["Groups"]
)

@router.get("")
async def get_groups(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của nhóm"),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm"),
    sort_by: Optional[str] = Query(None, description="Trường sắp xếp"),
    sort_order: str = Query("asc", description="Thứ tự sắp xếp (asc/desc)"),
    department_id: Optional[UUID] = Query(None, description="ID phòng ban"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_groups"))
):
    return await handle_group.get_groups(
        db=db,
        id=id,
        current_user=current_user,
        page=page,
        page_size=page_size,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        department_id=department_id
    )

@router.get("/{group_id}")
async def get_group_by_id(
    request: Request,
    group_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_group_by_id"))
):
    return await handle_group.get_group_by_id(group_id, db, current_user)

@router.post("")
@log_user_action("create_group")
async def create_group(
    group_data: GroupCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("create_group")),
):
    return await handle_group.create_group(group_data, db, current_user)

@router.put("/{group_id}")
@log_user_action("update_group")
async def update_group(
    group_id: UUID,
    group_data: GroupUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("edit_group")),
):
    return await handle_group.update_group(group_id, group_data, db, current_user)

@router.delete("/{group_id}")
@log_user_action("delete_group")
async def delete_group(
    group_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("delete_group")),
):
    return await handle_group.delete_group(group_id, db, current_user)

@router.get("/{group_id}/detail")
async def get_group_detail(
    request: Request,
    group_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_group_detail_by_id"))
):
    return await handle_group.get_group_detail(group_id, db, current_user)

