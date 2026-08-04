from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.core.config.database import get_db
from app.schemas.requests.ticket_flow_step import TicketFlowStepCreate, TicketFlowStepUpdate
from app.db.models import User
from app.core.security.permissions import has_permission
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.services.v1 import handle_ticket_flow_step
from uuid import UUID

router = APIRouter(
    prefix="/ticket-flow-steps",
    tags=["Ticket Flow Steps"]
)


@router.get("")
async def get_ticket_flow_steps(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của ticket flow step"),
    flow_id: Optional[UUID] = Query(None, description="Lọc theo flow ID"),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    sort_by: Optional[str] = Query(None, description="Trường sắp xếp (step_order, step_name, created_at)"),
    sort_order: str = Query("asc", description="Thứ tự sắp xếp (asc/desc)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_flow_steps"))
):
    """
    Lấy danh sách ticket flow steps với phân trang và filter
    
    - **id**: Lấy step cụ thể theo ID (nếu có)
    - **flow_id**: Lọc theo flow ID
    - **page**: Số trang (mặc định 1)
    - **page_size**: Số bản ghi mỗi trang (1-100, mặc định 10)
    - **sort_by**: Sắp xếp theo trường (step_order, step_name, created_at)
    - **sort_order**: Thứ tự sắp xếp (asc/desc, mặc định asc)
    
    **Lưu ý**: User chỉ thấy steps của flows thuộc tenant của mình (trừ super admin)
    """
    return await handle_ticket_flow_step.get_ticket_flow_steps(
        db=db,
        id=id,
        flow_id=flow_id,
        current_user=current_user,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order
    )


@router.get("/{step_id}")
async def get_ticket_flow_step_by_id(
    request: Request,
    step_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_flow_steps"))
):
    """
    Lấy thông tin chi tiết một ticket flow step theo ID
    
    - **step_id**: UUID của step cần xem
    
    **Lưu ý**: User chỉ xem được step của flow thuộc tenant của mình (trừ super admin)
    """
    return await handle_ticket_flow_step.get_ticket_flow_step_by_id(step_id, db, current_user)


@router.post("")
@log_user_action("create_ticket_flow_step")
async def create_ticket_flow_step(
    step_data: TicketFlowStepCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("create_ticket_flow_step")),
):
    """
    Tạo ticket flow step mới
    
    - **flow_id**: ID của flow (bắt buộc)
    - **step_name**: Tên của step (bắt buộc, 1-255 ký tự)
    - **step_order**: Thứ tự của step (bắt buộc, >= 1)
    - **assignee_user_id**: ID của user được gán (tùy chọn)
    - **assignee_group_id**: ID của group được gán (tùy chọn)
    
    **Lưu ý**: 
    - Step sẽ tự động được gán vào flow
    - step_order phải unique trong cùng flow
    - assignee_user_id và assignee_group_id phải thuộc cùng tenant với flow
    """
    return await handle_ticket_flow_step.create_ticket_flow_step(step_data, db, current_user)


@router.put("/{step_id}")
@log_user_action("update_ticket_flow_step")
async def update_ticket_flow_step(
    step_id: UUID,
    step_data: TicketFlowStepUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("edit_ticket_flow_step")),
):
    """
    Cập nhật thông tin ticket flow step
    
    - **step_id**: UUID của step cần cập nhật
    - **step_name**: Tên mới của step (tùy chọn)
    - **step_order**: Thứ tự mới (tùy chọn)
    - **assignee_user_id**: ID của user được gán (tùy chọn, có thể set None để xóa)
    - **assignee_group_id**: ID của group được gán (tùy chọn, có thể set None để xóa)
    
    **Lưu ý**: 
    - User chỉ cập nhật được step của flow thuộc tenant của mình (trừ super admin)
    - step_order mới phải unique trong cùng flow
    """
    return await handle_ticket_flow_step.update_ticket_flow_step(step_id, step_data, db, current_user)


@router.delete("/{step_id}")
@log_user_action("delete_ticket_flow_step")
async def delete_ticket_flow_step(
    step_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("delete_ticket_flow_step")),
):
    """
    Xóa ticket flow step
    
    - **step_id**: UUID của step cần xóa
    
    **Lưu ý**: 
    - Step sẽ bị xóa vĩnh viễn (hard delete)
    - Không thể xóa step đang được sử dụng bởi các flow instances
    - User chỉ xóa được step của flow thuộc tenant của mình (trừ super admin)
    """
    return await handle_ticket_flow_step.delete_ticket_flow_step(step_id, db, current_user)
