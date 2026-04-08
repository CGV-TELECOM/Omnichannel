from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config.database import get_db
from app.schemas.requests.ticket_context import (
    TicketContextCreate,
    TicketContextUpdate,
    TicketContextResponse
)
from app.db.models import User
from app.core.security.permissions import has_permission
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.services.v1 import handle_ticket_context
from uuid import UUID
from typing import Optional

router = APIRouter(
    prefix="/ticket-contexts",
    tags=["Ticket Contexts"]
)

@router.get("")
async def get_ticket_contexts(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của ticket context"),
    ticket_id: Optional[UUID] = Query(None, description="ID của ticket"),
    context_type: Optional[str] = Query(None, description="Loại context (customer, product, order, etc.)"),
    context_id: Optional[str] = Query(None, description="ID của context"),
    source_type: Optional[str] = Query(None, description="Nguồn của context (crm, erp, call_system, etc.)"),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    sort_by: Optional[str] = Query(None, description="Trường sắp xếp"),
    sort_order: str = Query("desc", description="Thứ tự sắp xếp (asc/desc)"),
    tenant_id: Optional[UUID] = Query(None, description="ID của tenant (chỉ Super Admin)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_contexts"))
):
    """
    Lấy danh sách ticket contexts với pagination và filtering
    
    - **id**: Lọc theo ID của context
    - **ticket_id**: Lọc theo ID của ticket
    - **context_type**: Lọc theo loại context (customer, product, order, call, email, etc.)
    - **context_id**: Lọc theo ID của context
    - **source_type**: Lọc theo nguồn (crm, erp, call_system, email_system, etc.)
    - **page**: Số trang (mặc định: 1)
    - **page_size**: Số bản ghi mỗi trang (mặc định: 10, tối đa: 100)
    - **sort_by**: Trường để sắp xếp (created_at, context_type, etc.)
    - **sort_order**: Thứ tự sắp xếp (asc/desc, mặc định: desc)
    - **tenant_id**: ID của tenant (chỉ Super Admin)
    """
    return await handle_ticket_context.get_ticket_contexts(
        db=db,
        current_user=current_user,
        id=id,
        ticket_id=ticket_id,
        context_type=context_type,
        context_id=context_id,
        source_type=source_type,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        tenant_id=tenant_id
    )


@router.get("/{context_id}")
async def get_ticket_context_by_id(
    request: Request,
    context_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_contexts"))
):
    """
    Lấy thông tin chi tiết một ticket context theo ID
    
    - **context_id**: ID của ticket context cần lấy
    """
    return await handle_ticket_context.get_ticket_context_by_id(context_id, db, current_user)


@router.post("")
@log_user_action("create_ticket_context")
async def create_ticket_context(
    context_data: TicketContextCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("create_ticket_context")),
):
    """
    Tạo ticket context mới
    
    - **ticket_id**: ID của ticket (bắt buộc)
    - **context_type**: Loại context (bắt buộc, ví dụ: customer, product, order, call, email)
    - **context_id**: ID của context (bắt buộc, ví dụ: customer_id, product_id, order_id)
    - **source_type**: Nguồn của context (tùy chọn, ví dụ: crm, erp, call_system, email_system)
    - **context_metadata**: Metadata bổ sung dạng JSON (tùy chọn)
    - **tenant_id**: ID của tenant (chỉ Super Admin mới có thể chỉ định)
    
    **Ví dụ use cases:**
    - Link ticket với customer: `context_type="customer", context_id="CUST001"`
    - Link ticket với order: `context_type="order", context_id="ORD123456"`
    - Link ticket với cuộc gọi: `context_type="call", context_id="CALL789", source_type="call_system"`
    """
    return await handle_ticket_context.create_ticket_context(context_data, db, current_user)


@router.put("/{context_id}")
@log_user_action("update_ticket_context")
async def update_ticket_context(
    context_id: UUID,
    context_data: TicketContextUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("edit_ticket_context")),
):
    """
    Cập nhật ticket context
    
    - **context_id**: ID của ticket context cần cập nhật
    - **context_type**: Loại context (tùy chọn)
    - **context_id**: ID của context (tùy chọn)
    - **source_type**: Nguồn của context (tùy chọn)
    - **context_metadata**: Metadata bổ sung (tùy chọn)
    """
    return await handle_ticket_context.update_ticket_context(context_id, context_data, db, current_user)


@router.delete("/{context_id}")
@log_user_action("delete_ticket_context")
async def delete_ticket_context(
    context_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("delete_ticket_context")),
):
    """
    Xóa ticket context
    
    - **context_id**: ID của ticket context cần xóa
    
    Lưu ý: Đây là hard delete, context sẽ bị xóa vĩnh viễn khỏi database
    """
    return await handle_ticket_context.delete_ticket_context(context_id, db, current_user)


@router.get("/ticket/{ticket_id}/contexts")
async def get_contexts_by_ticket(
    request: Request,
    ticket_id: UUID,
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(20, ge=1, le=100, description="Số bản ghi mỗi trang"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_contexts"))
):
    """
    Lấy tất cả contexts của một ticket
    
    - **ticket_id**: ID của ticket
    - **page**: Số trang (mặc định: 1)
    - **page_size**: Số bản ghi mỗi trang (mặc định: 20, tối đa: 100)
    
    Endpoint này hữu ích để xem tất cả thông tin liên quan đến một ticket:
    - Khách hàng nào
    - Đơn hàng nào
    - Cuộc gọi nào
    - Email nào
    - etc.
    """
    return await handle_ticket_context.get_ticket_contexts(
        db=db,
        current_user=current_user,
        ticket_id=ticket_id,
        page=page,
        page_size=page_size,
        sort_by="created_at",
        sort_order="asc"
    )
