from fastapi import APIRouter, Depends, Request, Query
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config.database import get_db
from app.schemas.requests.department import DepartmentCreate, DepartmentUpdate
from app.db.models import Department, User
from app.core.security.permissions import has_permission
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.services.v1 import handle_department
from uuid import UUID

router = APIRouter(
    prefix="/departments",
    tags=["Departments"]
)

@router.get("")
async def get_departments(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của phòng ban"),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm"),
    sort_by: Optional[str] = Query(None, description="Trường sắp xếp"),
    sort_order: str = Query("asc", description="Thứ tự sắp xếp (asc/desc)"),
    current_user: User = Depends(get_current_user_dependency),
    db: AsyncSession = Depends(get_db),
    _ = Depends(has_permission("view_departments"))
):
    return await handle_department.get_departments(
        db=db,
        id=id,
        page=page,
        page_size=page_size,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        current_user=current_user
    )

@router.get("/{department_id}")
async def get_department_by_id(
    request: Request,
    department_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_department_by_id"))
    
):
    return await handle_department.get_department_by_id(department_id, db, current_user)

@router.get("/{department_id}/detail")
async def get_department_detail(
    request: Request,
    department_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_department_by_id"))
):
    return await handle_department.get_department_detail(department_id, db, current_user)

@router.post("")
@log_user_action("create_department")
async def create_department(
    department_data: DepartmentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("create_department")),
):
    return await handle_department.create_department(department_data, db, current_user)

@router.put("/{department_id}")
@log_user_action("update_department")
async def update_department(
    department_id: UUID,
    department_data: DepartmentUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("edit_department")),
):
    return await handle_department.update_department(department_id, department_data, db, current_user)

@router.delete("/{department_id}")
@log_user_action("delete_department")
async def delete_department(
    department_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("delete_department")),
):
    return await handle_department.delete_department(department_id, db, current_user)


