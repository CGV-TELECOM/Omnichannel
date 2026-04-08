from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config.database import get_db
from app.schemas.requests.ticket import (
    TicketCreate,
    TicketUpdate,
    TicketAssign,
    TicketStatusUpdate
)
from app.db.models import User
from app.core.security.permissions import has_permission
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.services.v1 import handle_ticket
from uuid import UUID
from typing import Optional, List

router = APIRouter(
    prefix="/tickets",
    tags=["Tickets"]
)


@router.get("")
async def get_tickets(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của ticket"),
    code: Optional[str] = Query(None, description="Mã ticket"),
    status: Optional[str] = Query(None, description="Trạng thái (pending, open, in_progress, on_hold, resolved, closed, cancelled)"),
    priority: Optional[str] = Query(None, description="Mức độ ưu tiên (low, medium, high, urgent, critical)"),
    template_id: Optional[UUID] = Query(None, description="ID của template"),
    flow_id: Optional[UUID] = Query(None, description="ID của flow"),
    created_by: Optional[UUID] = Query(None, description="ID của người tạo"),
    assigned_to: Optional[UUID] = Query(None, description="ID của người được gán"),
    tag_ids: Optional[List[UUID]] = Query(None, description="Danh sách ID của tags"),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    search: Optional[str] = Query(None, description="Tìm kiếm theo tiêu đề, mô tả, hoặc mã ticket"),
    sort_by: Optional[str] = Query(None, description="Trường sắp xếp (created_at, code, title, status, priority)"),
    sort_order: str = Query("desc", description="Thứ tự sắp xếp (asc/desc)"),
    tenant_id: Optional[UUID] = Query(None, description="ID của tenant (chỉ Super Admin)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_tickets"))
):
    """
    Lấy danh sách tickets với pagination và filtering
    
    **Filters:**
    - **id**: Lọc theo ID của ticket
    - **code**: Tìm kiếm theo mã ticket
    - **status**: Lọc theo trạng thái (pending, open, in_progress, on_hold, resolved, closed, cancelled)
    - **priority**: Lọc theo mức độ ưu tiên (low, medium, high, urgent, critical)
    - **template_id**: Lọc theo template
    - **flow_id**: Lọc theo flow
    - **created_by**: Lọc theo người tạo
    - **assigned_to**: Lọc theo người được gán
    - **tag_ids**: Lọc theo tags
    
    **Search:**
    - **search**: Tìm kiếm trong tiêu đề, mô tả, và mã ticket
    
    **Pagination:**
    - **page**: Số trang (mặc định: 1)
    - **page_size**: Số bản ghi mỗi trang (mặc định: 10, tối đa: 100)
    
    **Sorting:**
    - **sort_by**: Trường để sắp xếp (created_at, code, title, status, priority)
    - **sort_order**: Thứ tự sắp xếp (asc/desc, mặc định: desc)
    """
    return await handle_ticket.get_tickets(
        db=db,
        current_user=current_user,
        id=id,
        code=code,
        status=status,
        priority=priority,
        template_id=template_id,
        flow_id=flow_id,
        created_by=created_by,
        assigned_to=assigned_to,
        tag_ids=tag_ids,
        page=page,
        page_size=page_size,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        tenant_id=tenant_id
    )


@router.get("/{ticket_id}")
async def get_ticket_by_id(
    request: Request,
    ticket_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_tickets"))
):
    """
    Lấy thông tin chi tiết ticket theo ID
    
    Trả về thông tin đầy đủ của ticket bao gồm:
    - Thông tin cơ bản
    - Template và Flow (nếu có)
    - Tags
    - Extension data
    - User names (created_by, assigned_to)
    """
    return await handle_ticket.get_ticket_by_id(ticket_id, db, current_user)


@router.get("/code/{code}")
async def get_ticket_by_code(
    request: Request,
    code: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_tickets"))
):
    """
    Lấy thông tin chi tiết ticket theo code
    
    Trả về thông tin đầy đủ của ticket bao gồm:
    - Thông tin cơ bản
    - Template và Flow (nếu có)
    - Tags
    - Extension data
    - User names (created_by, assigned_to)
    """
    return await handle_ticket.get_ticket_by_code(code, db, current_user)


@router.post("")
@log_user_action("create_ticket")
async def create_ticket(
    ticket_data: TicketCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("create_ticket")),
):
    """
    Tạo ticket mới
    
    **Required fields:**
    - **title**: Tiêu đề ticket (bắt buộc)
    
    **Optional fields:**
    - **description**: Mô tả chi tiết
    - **priority**: Mức độ ưu tiên (low, medium, high, urgent, critical). Mặc định: medium
    - **template_id**: ID của template (nếu có)
    - **flow_id**: ID của flow (nếu có)
    - **sla_id**: ID của SLA (nếu có)
    - **assigned_to**: ID của user được gán
    - **extension_data**: Dữ liệu mở rộng theo template (JSON)
    - **tag_ids**: Danh sách ID của tags
    - **tenant_id**: ID của tenant (chỉ Super Admin)
    
    **Auto-generated:**
    - **code**: Mã ticket tự động (format: TKT-YYYY-XXXX)
    - **status**: Mặc định là "pending"
    - **created_by**: User hiện tại
    - **created_at**: Thời gian tạo
    
    **Events:**
    - Tự động tạo event "CREATED"
    """
    return await handle_ticket.create_ticket(ticket_data, db, current_user)


@router.put("/{ticket_id}")
@log_user_action("update_ticket")
async def update_ticket(
    ticket_id: UUID,
    ticket_data: TicketUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("edit_ticket")),
):
    """
    Cập nhật thông tin ticket
    
    **Updatable fields:**
    - **title**: Tiêu đề ticket
    - **description**: Mô tả chi tiết
    - **status**: Trạng thái (pending, open, in_progress, on_hold, resolved, closed, cancelled)
    - **priority**: Mức độ ưu tiên (low, medium, high, urgent, critical)
    - **assigned_to**: ID của user được gán
    - **sla_id**: ID của SLA
    - **extension_data**: Dữ liệu mở rộng (JSON)
    - **tag_ids**: Danh sách ID của tags
    
    **Notes:**
    - Chỉ cập nhật các trường được cung cấp (partial update)
    - Khi status chuyển thành "closed", tự động set closed_at
    - Tự động tạo event "UPDATED" với thông tin thay đổi
    """
    return await handle_ticket.update_ticket(ticket_id, ticket_data, db, current_user)


@router.delete("/{ticket_id}")
@log_user_action("delete_ticket")
async def delete_ticket(
    ticket_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("delete_ticket")),
):
    """
    Xóa ticket
    
    **Warning:**
    - Đây là hard delete (xóa vĩnh viễn)
    - Sẽ xóa toàn bộ dữ liệu liên quan (extensions, events, contexts, flow instances) do cascade
    - Không thể khôi phục sau khi xóa
    
    **Permissions:**
    - Cần quyền "delete_ticket"
    - Chỉ có thể xóa ticket trong tenant của mình (trừ Super Admin)
    """
    return await handle_ticket.delete_ticket(ticket_id, db, current_user)


@router.post("/{ticket_id}/assign")
@log_user_action("assign_ticket")
async def assign_ticket(
    ticket_id: UUID,
    assign_data: TicketAssign,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("assign_ticket")),
):
    """
    Gán ticket cho user
    
    **Required:**
    - **assigned_to**: ID của user được gán (bắt buộc)
    
    **Validation:**
    - User phải tồn tại
    - User phải cùng tenant với ticket
    - User phải đang hoạt động (is_active = 1)
    
    **Events:**
    - Tự động tạo event "ASSIGNED" với thông tin người gán cũ và mới
    """
    return await handle_ticket.assign_ticket(ticket_id, assign_data, db, current_user)


@router.post("/{ticket_id}/status")
@log_user_action("update_ticket_status")
async def update_ticket_status(
    ticket_id: UUID,
    status_data: TicketStatusUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("edit_ticket")),
):
    """
    Cập nhật trạng thái ticket
    
    **Required:**
    - **status**: Trạng thái mới (pending, open, in_progress, on_hold, resolved, closed, cancelled)
    
    **Optional:**
    - **note**: Ghi chú về thay đổi trạng thái
    
    **Auto-actions:**
    - Khi status = "closed": Tự động set closed_at = now
    - Khi status != "closed" và old_status = "closed": Clear closed_at (reopen)
    
    **Events:**
    - Status = "closed": Tạo event "CLOSED"
    - Old status = "closed" và new status != "closed": Tạo event "REOPENED"
    - Các trường hợp khác: Tạo event "STATUS_CHANGED"
    """
    return await handle_ticket.update_ticket_status(ticket_id, status_data, db, current_user)
