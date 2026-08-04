from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.core.config.database import get_db
from app.schemas.requests.ticket_flow import TicketFlowCreate, TicketFlowUpdate
from app.db.models import User
from app.core.security.permissions import has_permission
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.services.v1 import handle_ticket_flow
from uuid import UUID

router = APIRouter(
    prefix="/ticket-flows",
    tags=["Ticket Flows"]
)


@router.get("")
async def get_ticket_flows(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của ticket flow"),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm (tên hoặc mô tả)"),
    sort_by: Optional[str] = Query(None, description="Trường sắp xếp (name, created_at, updated_at)"),
    sort_order: str = Query("asc", description="Thứ tự sắp xếp (asc/desc)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_flows"))
):
    """
    Lấy danh sách ticket flows với phân trang và tìm kiếm
    
    - **id**: Lấy flow cụ thể theo ID (nếu có)
    - **page**: Số trang (mặc định 1)
    - **page_size**: Số bản ghi mỗi trang (1-100, mặc định 10)
    - **search**: Tìm kiếm theo tên hoặc mô tả
    - **sort_by**: Sắp xếp theo trường (name, created_at, updated_at)
    - **sort_order**: Thứ tự sắp xếp (asc/desc)
    
    **Lưu ý**: User chỉ thấy flows thuộc tenant của mình (trừ super admin)
    """
    return await handle_ticket_flow.get_ticket_flows(
        db=db,
        id=id,
        current_user=current_user,
        page=page,
        page_size=page_size,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order
    )


@router.get("/{flow_id}")
async def get_ticket_flow_by_id(
    request: Request,
    flow_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_ticket_flows"))
):
    """
    Lấy thông tin chi tiết một ticket flow theo ID
    
    - **flow_id**: UUID của flow cần xem
    
    **Lưu ý**: User chỉ xem được flow thuộc tenant của mình (trừ super admin)
    """
    return await handle_ticket_flow.get_ticket_flow_by_id(flow_id, db, current_user)


@router.post("")
@log_user_action("create_ticket_flow")
async def create_ticket_flow(
    flow_data: TicketFlowCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("create_ticket_flow")),
):
    """
    Tạo ticket flow mới
    
    - **name**: Tên flow (bắt buộc, 1-255 ký tự)
    - **description**: Mô tả flow (tùy chọn)
    - **tenant_id**: ID của tenant (tùy chọn, chỉ Super Admin mới có thể chỉ định)
    
    **Lưu ý**: 
    - Flow sẽ tự động được gán tenant_id từ user hiện tại (trừ super admin)
    - Tên flow phải unique trong cùng tenant
    """
    return await handle_ticket_flow.create_ticket_flow(flow_data, db, current_user)


@router.put("/{flow_id}")
@log_user_action("update_ticket_flow")
async def update_ticket_flow(
    flow_id: UUID,
    flow_data: TicketFlowUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("edit_ticket_flow")),
):
    """
    Cập nhật thông tin ticket flow
    
    - **flow_id**: UUID của flow cần cập nhật
    - **name**: Tên flow mới (tùy chọn)
    - **description**: Mô tả mới (tùy chọn)
    
    **Lưu ý**: User chỉ cập nhật được flow thuộc tenant của mình (trừ super admin)
    """
    return await handle_ticket_flow.update_ticket_flow(flow_id, flow_data, db, current_user)


@router.delete("/{flow_id}")
@log_user_action("delete_ticket_flow")
async def delete_ticket_flow(
    flow_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("delete_ticket_flow")),
):
    """
    Xóa ticket flow
    
    - **flow_id**: UUID của flow cần xóa
    
    **Lưu ý**: 
    - Flow sẽ bị xóa vĩnh viễn (hard delete)
    - Các steps và flow instances liên quan sẽ bị xóa theo (cascade)
    - Không thể xóa flow đang được sử dụng bởi các ticket đang hoạt động
    - User chỉ xóa được flow thuộc tenant của mình (trừ super admin)
    """
    return await handle_ticket_flow.delete_ticket_flow(flow_id, db, current_user)
