from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.core.config.database import get_db
from app.schemas.requests.ticket_flow_instance import TicketFlowInstanceCreate, TicketFlowInstanceUpdate
from app.db.models import User
from app.core.security.permissions import has_permission
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.services.v1 import handle_ticket_flow_instance
from uuid import UUID

router = APIRouter(
    prefix="/ticket-flow-instances",
    tags=["Ticket Flow Instances"]
)


@router.get("")
async def get_ticket_flow_instances(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của ticket flow instance"),
    ticket_id: Optional[UUID] = Query(None, description="Lọc theo ticket ID"),
    flow_id: Optional[UUID] = Query(None, description="Lọc theo flow ID"),
    current_step_id: Optional[UUID] = Query(None, description="Lọc theo current step ID"),
    status: Optional[str] = Query(None, description="Lọc theo trạng thái (pending, running, paused, completed, failed, cancelled)"),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    sort_by: Optional[str] = Query(None, description="Trường sắp xếp (started_at, finished_at, status)"),
    sort_order: str = Query("desc", description="Thứ tự sắp xếp (asc/desc)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_flow_instances"))
):
    """
    Lấy danh sách ticket flow instances với phân trang và filter
    
    - **id**: Lấy instance cụ thể theo ID (nếu có)
    - **ticket_id**: Lọc theo ticket ID
    - **flow_id**: Lọc theo flow ID
    - **status**: Lọc theo trạng thái
    - **page**: Số trang (mặc định 1)
    - **page_size**: Số bản ghi mỗi trang (1-100, mặc định 10)
    - **sort_by**: Sắp xếp theo trường (started_at, finished_at, status)
    - **sort_order**: Thứ tự sắp xếp (asc/desc, mặc định desc)
    
    **Lưu ý**: User chỉ thấy instances thuộc tenant của mình (trừ super admin)
    """
    return await handle_ticket_flow_instance.get_ticket_flow_instances(
        db=db,
        id=id,
        ticket_id=ticket_id,
        flow_id=flow_id,
        current_step_id=current_step_id,
        status=status,
        current_user=current_user,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order
    )


@router.get("/{instance_id}")
async def get_ticket_flow_instance_by_id(
    request: Request,
    instance_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_flow_instances"))
):
    """
    Lấy thông tin chi tiết một ticket flow instance theo ID
    
    - **instance_id**: UUID của instance cần xem
    
    **Lưu ý**: User chỉ xem được instance thuộc tenant của mình (trừ super admin)
    """
    return await handle_ticket_flow_instance.get_ticket_flow_instance_by_id(instance_id, db, current_user)


@router.post("")
@log_user_action("create_ticket_flow_instance")
async def create_ticket_flow_instance(
    instance_data: TicketFlowInstanceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("create_ticket_flow_instance")),
):
    """
    Tạo ticket flow instance mới
    
    - **ticket_id**: ID của ticket (bắt buộc)
    - **flow_id**: ID của flow (bắt buộc)
    - **current_step_id**: ID của step hiện tại (tùy chọn)
    - **status**: Trạng thái (mặc định: pending)
    - **tenant_id**: ID của tenant (tùy chọn, chỉ Super Admin mới có thể chỉ định)
    
    **Lưu ý**: 
    - Instance sẽ tự động được gán tenant_id từ user hiện tại (trừ super admin)
    - Một ticket chỉ có thể có một flow instance đang chạy (pending/running/paused) tại một thời điểm
    """
    return await handle_ticket_flow_instance.create_ticket_flow_instance(instance_data, db, current_user)


@router.put("/{instance_id}")
@log_user_action("update_ticket_flow_instance")
async def update_ticket_flow_instance(
    instance_id: UUID,
    instance_data: TicketFlowInstanceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("edit_ticket_flow_instance")),
):
    """
    Cập nhật thông tin ticket flow instance
    
    - **instance_id**: UUID của instance cần cập nhật
    - **current_step_id**: ID của step hiện tại (tùy chọn)
    - **status**: Trạng thái mới (tùy chọn)
    - **finished_at**: Thời gian hoàn thành (tùy chọn)
    
    **Lưu ý**: 
    - User chỉ cập nhật được instance thuộc tenant của mình (trừ super admin)
    - Khi status chuyển sang completed/failed/cancelled, finished_at sẽ tự động được set
    """
    return await handle_ticket_flow_instance.update_ticket_flow_instance(instance_id, instance_data, db, current_user)


@router.delete("/{instance_id}")
@log_user_action("delete_ticket_flow_instance")
async def delete_ticket_flow_instance(
    instance_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("delete_ticket_flow_instance")),
):
    """
    Xóa ticket flow instance
    
    - **instance_id**: UUID của instance cần xóa
    
    **Lưu ý**: 
    - Instance sẽ bị xóa vĩnh viễn (hard delete)
    - User chỉ xóa được instance thuộc tenant của mình (trừ super admin)
    """
    return await handle_ticket_flow_instance.delete_ticket_flow_instance(instance_id, db, current_user)
