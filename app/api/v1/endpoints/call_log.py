from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config.database import get_db
from app.schemas.requests.call_log import (
    CallLogCreate,
    CallLogUpdate,
)
from app.db.models import User
from app.core.dependencies.dependencies import get_current_user_dependency
from app.services.v1 import handle_call_log
from uuid import UUID
from typing import Optional

router = APIRouter(
    prefix="/call-logs",
    tags=["Call Logs"]
)

@router.post("")
async def create_call(
    request: Request,
    call_log_data: CallLogCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency)
):
    """
    Tạo bản ghi cuộc gọi mới (CallLog) — dùng khi agent gọi outbound từ web.
    sip_call_id phải là UUID (khóa map với tổng đài).
    """
    return await handle_call_log.create_call_log(db=db, current_user=current_user, data=call_log_data)

@router.put("/{sip_call_id}")
async def update_call(
    sip_call_id: UUID,
    request: Request,
    call_log_data: CallLogUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency)
):
    """
    Cập nhật trạng thái cuộc gọi theo sip_call_id
    """
    return await handle_call_log.update_call_log(sip_call_id=sip_call_id, db=db, current_user=current_user, data=call_log_data)

@router.get("/{sip_call_id}/events")
async def list_call_events(
    sip_call_id: UUID,
    request: Request,
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(50, ge=1, le=200, description="Số event mỗi trang"),
    state: Optional[str] = Query(None, description="Lọc theo state (ringing|answered|hangup|cdr|...)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """
    Timeline call events (raw webhook) của 1 cuộc gọi theo sip_call_id.
    """
    return await handle_call_log.get_call_log_events(
        sip_call_id=sip_call_id,
        db=db,
        current_user=current_user,
        page=page,
        page_size=page_size,
        state=state,
    )


@router.get("/{sip_call_id}/events/{event_id}")
async def get_call_event(
    sip_call_id: UUID,
    event_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
):
    """
    Chi tiết 1 call event theo id (thuộc cuộc gọi sip_call_id).
    """
    return await handle_call_log.get_call_log_event_by_id(
        sip_call_id=sip_call_id,
        event_id=event_id,
        db=db,
        current_user=current_user,
    )


@router.get("/{sip_call_id}")
async def get_call_by_id(
    sip_call_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency)
):
    """
    Lấy thông tin chi tiết cuộc gọi qua sip_call_id (Tra cứu ngược)
    """
    return await handle_call_log.get_call_log_by_sip_call_id(sip_call_id=sip_call_id, db=db, current_user=current_user)

@router.get("")
async def list_calls(
    request: Request,
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    search: Optional[str] = Query(None, description="Tìm kiếm theo số điện thoại hoặc sip_call_id"),
    direction: Optional[str] = Query(None, description="Chiều cuộc gọi (inbound/outbound)"),
    status: Optional[str] = Query(None, description="Trạng thái"),
    tenant_id: Optional[UUID] = Query(None, description="ID Tenant (chỉ Super Admin)"),
    ticket_id: Optional[UUID] = Query(None, description="ID Ticket"),
    customer_id: Optional[UUID] = Query(None, description="ID Khách hàng"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency)
):
    """
    Lấy danh sách các cuộc gọi có phân trang và lọc
    """
    return await handle_call_log.get_call_logs(
        db=db,
        current_user=current_user,
        page=page,
        page_size=page_size,
        search=search,
        direction=direction,
        status=status,
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        customer_id=customer_id
    )
