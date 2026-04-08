from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config.database import get_db
from app.schemas.requests.ticket_event import (
    TicketEventCreate, 
    TicketEventUpdate, 
    TicketEventResponse,
    TicketEventFilter
)
from app.db.models import User
from app.core.security.permissions import has_permission
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.services.v1 import handle_ticket_event
from uuid import UUID
from typing import Optional
from datetime import datetime

router = APIRouter(
    prefix="/ticket-events",
    tags=["Ticket Events"]
)

@router.get("")
async def get_ticket_events(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của ticket event"),
    ticket_id: Optional[UUID] = Query(None, description="ID của ticket"),
    event_type: Optional[str] = Query(None, description="Loại sự kiện (CREATED, UPDATED, REOPENED, CLOSED, etc.)"),
    actor_type: Optional[str] = Query(None, description="Tên role của actor (admin, user, manager, etc.)"),
    actor_id: Optional[str] = Query(None, description="ID của actor (string: UUID, 'system', 'api', etc.)"),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    sort_by: Optional[str] = Query(None, description="Trường sắp xếp"),
    sort_order: str = Query("desc", description="Thứ tự sắp xếp (asc/desc)"),
    tenant_id: Optional[UUID] = Query(None, description="ID của tenant (chỉ Super Admin mới có thể chỉ định)"),
    from_date: Optional[datetime] = Query(None, description="Lọc từ ngày"),
    to_date: Optional[datetime] = Query(None, description="Lọc đến ngày"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_events"))
):
    """
    Lấy danh sách ticket events với pagination và filtering
    
    - **id**: Lọc theo ID của ticket event
    - **ticket_id**: Lọc theo ID của ticket
    - **event_type**: Lọc theo loại sự kiện (CREATED, UPDATED, REOPENED, CLOSED, etc.)
    - **actor_type**: Lọc theo tên role của actor (admin, user, manager, etc.)
    - **actor_id**: Lọc theo ID của actor (string: UUID, 'system', 'api', etc.)
    - **page**: Số trang (mặc định: 1)
    - **page_size**: Số bản ghi mỗi trang (mặc định: 10, tối đa: 100)
    - **sort_by**: Trường để sắp xếp (created_at, event_type, etc.)
    - **sort_order**: Thứ tự sắp xếp (asc/desc, mặc định: desc)
    - **tenant_id**: ID của tenant (chỉ Super Admin)
    - **from_date**: Lọc từ ngày
    - **to_date**: Lọc đến ngày
    """
    return await handle_ticket_event.get_ticket_events(
        db=db,
        current_user=current_user,
        id=id,
        ticket_id=ticket_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        tenant_id=tenant_id,
        from_date=from_date,
        to_date=to_date
    )


@router.get("/{event_id}")
async def get_ticket_event_by_id(
    request: Request,
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_events"))
):
    """
    Lấy thông tin chi tiết một ticket event theo ID
    
    - **event_id**: ID của ticket event cần lấy
    """
    return await handle_ticket_event.get_ticket_event_by_id(event_id, db, current_user)


@router.post("")
@log_user_action("create_ticket_event")
async def create_ticket_event(
    event_data: TicketEventCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("create_ticket_event")),
):
    """
    Tạo ticket event mới
    
    - **ticket_id**: ID của ticket (bắt buộc)
    - **event_type**: Loại sự kiện (bắt buộc, ví dụ: CREATED, UPDATED, REOPENED, CLOSED, ASSIGNED, COMMENTED)
    - **payload**: Dữ liệu chi tiết của sự kiện dưới dạng JSON (tùy chọn)
    - **actor_id**: ID của actor dạng string (tùy chọn, mặc định là user_id của user hiện tại. Có thể là UUID, 'system', 'api', etc.)
    - **tenant_id**: ID của tenant (chỉ Super Admin mới có thể chỉ định)
    
    **Lưu ý**: `actor_type` sẽ tự động được gán bằng tên role của user hiện tại
    """
    return await handle_ticket_event.create_ticket_event(event_data, db, current_user)


@router.put("/{event_id}")
@log_user_action("update_ticket_event")
async def update_ticket_event(
    event_id: UUID,
    event_data: TicketEventUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("edit_ticket_event")),
):
    """
    Cập nhật ticket event
    
    - **event_id**: ID của ticket event cần cập nhật
    - **event_type**: Loại sự kiện (tùy chọn, ví dụ: CREATED, UPDATED, REOPENED, CLOSED)
    - **payload**: Dữ liệu chi tiết của sự kiện (tùy chọn)
    
    **Lưu ý**: `actor_type` và `actor_id` không thể cập nhật sau khi tạo
    """
    return await handle_ticket_event.update_ticket_event(event_id, event_data, db, current_user)


@router.delete("/{event_id}")
@log_user_action("delete_ticket_event")
async def delete_ticket_event(
    event_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("delete_ticket_event")),
):
    """
    Xóa ticket event
    
    - **event_id**: ID của ticket event cần xóa
    
    Lưu ý: Đây là hard delete, ticket event sẽ bị xóa vĩnh viễn khỏi database
    """
    return await handle_ticket_event.delete_ticket_event(event_id, db, current_user)


@router.get("/ticket/{ticket_id}/timeline")
async def get_ticket_timeline(
    request: Request,
    ticket_id: UUID,
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(20, ge=1, le=100, description="Số bản ghi mỗi trang"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_events"))
):
    """
    Lấy timeline của một ticket (tất cả events của ticket đó theo thứ tự thời gian)
    
    - **ticket_id**: ID của ticket
    - **page**: Số trang (mặc định: 1)
    - **page_size**: Số bản ghi mỗi trang (mặc định: 20, tối đa: 100)
    """
    return await handle_ticket_event.get_ticket_events(
        db=db,
        current_user=current_user,
        ticket_id=ticket_id,
        page=page,
        page_size=page_size,
        sort_by="created_at",
        sort_order="asc"  # Chronological order
    )
