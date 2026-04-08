from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config.database import get_db
from app.schemas.requests.ticket_template import (
    TicketTemplateCreate,
    TicketTemplateUpdate,
    TicketTemplateResponse
)
from app.db.models import User
from app.core.security.permissions import has_permission
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.services.v1 import handle_ticket_template
from uuid import UUID
from typing import Optional

router = APIRouter(
    prefix="/ticket-templates",
    tags=["Ticket Templates"]
)

@router.get("")
async def get_ticket_templates(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của ticket template"),
    name: Optional[str] = Query(None, description="Tên template"),
    is_active: Optional[bool] = Query(None, description="Trạng thái hoạt động"),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    search: Optional[str] = Query(None, description="Tìm kiếm theo tên hoặc mô tả"),
    sort_by: Optional[str] = Query(None, description="Trường sắp xếp"),
    sort_order: str = Query("desc", description="Thứ tự sắp xếp (asc/desc)"),
    tenant_id: Optional[UUID] = Query(None, description="ID của tenant (chỉ Super Admin)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_templates"))
):
    """
    Lấy danh sách ticket templates với pagination và filtering
    
    - **id**: Lọc theo ID của template
    - **name**: Lọc theo tên template
    - **is_active**: Lọc theo trạng thái hoạt động (true/false)
    - **search**: Tìm kiếm theo tên hoặc mô tả
    - **page**: Số trang (mặc định: 1)
    - **page_size**: Số bản ghi mỗi trang (mặc định: 10, tối đa: 100)
    - **sort_by**: Trường để sắp xếp (created_at, name, etc.)
    - **sort_order**: Thứ tự sắp xếp (asc/desc, mặc định: desc)
    - **tenant_id**: ID của tenant (chỉ Super Admin)
    """
    return await handle_ticket_template.get_ticket_templates(
        db=db,
        current_user=current_user,
        id=id,
        name=name,
        is_active=is_active,
        page=page,
        page_size=page_size,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        tenant_id=tenant_id
    )


@router.get("/{template_id}")
async def get_ticket_template_by_id(
    request: Request,
    template_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_templates"))
):
    """
    Lấy thông tin chi tiết một ticket template theo ID
    
    - **template_id**: ID của ticket template cần lấy
    """
    return await handle_ticket_template.get_ticket_template_by_id(template_id, db, current_user)


@router.post("")
@log_user_action("create_ticket_template")
async def create_ticket_template(
    template_data: TicketTemplateCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("create_ticket_template")),
):
    """
    Tạo ticket template mới
    
    - **name**: Tên template (bắt buộc)
    - **description**: Mô tả template (tùy chọn)
    - **flow_id**: ID của flow (tùy chọn)
    - **sla_id**: ID của SLA (tùy chọn)
    - **extension_schema**: Schema mở rộng dạng JSON (tùy chọn)
    - **is_active**: Trạng thái hoạt động (mặc định: true)
    - **tenant_id**: ID của tenant (chỉ Super Admin mới có thể chỉ định)
    """
    return await handle_ticket_template.create_ticket_template(template_data, db, current_user)


@router.put("/{template_id}")
@log_user_action("update_ticket_template")
async def update_ticket_template(
    template_id: UUID,
    template_data: TicketTemplateUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("edit_ticket_template")),
):
    """
    Cập nhật ticket template
    
    - **template_id**: ID của ticket template cần cập nhật
    - **name**: Tên template (tùy chọn)
    - **description**: Mô tả template (tùy chọn)
    - **flow_id**: ID của flow (tùy chọn)
    - **sla_id**: ID của SLA (tùy chọn)
    - **extension_schema**: Schema mở rộng dạng JSON (tùy chọn)
    - **is_active**: Trạng thái hoạt động (tùy chọn)
    """
    return await handle_ticket_template.update_ticket_template(template_id, template_data, db, current_user)


@router.delete("/{template_id}")
@log_user_action("delete_ticket_template")
async def delete_ticket_template(
    template_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("delete_ticket_template")),
):
    """
    Xóa ticket template (soft delete - set is_active = False)
    
    - **template_id**: ID của ticket template cần xóa
    """
    return await handle_ticket_template.delete_ticket_template(template_id, db, current_user)
