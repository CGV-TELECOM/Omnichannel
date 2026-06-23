from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.security.permissions import has_permission
from app.db.models import User
from app.schemas.requests.customer_provided_info import (
    CustomerProvidedInfoCreate,
    CustomerProvidedInfoUpdate,
)
from app.services.v1 import handle_customer_provided_info

router = APIRouter(
    prefix="/customer-provided-info",
    tags=["Customer Provided Info"],
)


@router.get("")
async def get_customer_provided_info_list(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của thông tin được cung cấp"),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm (tên / email / số điện thoại / mô tả)"),
    sort_by: Optional[str] = Query(None, description="Trường sắp xếp (name, email, phone, created_at, updated_at)"),
    sort_order: str = Query("desc", description="Thứ tự sắp xếp (asc/desc)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("view_customer_provided_info")),
):
    """
    Lấy danh sách thông tin KH cung cấp với phân trang, tìm kiếm.
    Nếu truyền `id` thì trả về luôn chi tiết 1 bản ghi.
    """
    return await handle_customer_provided_info.get_customer_provided_info(
        db=db,
        current_user=current_user,
        id=id,
        page=page,
        page_size=page_size,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/{info_id}")
async def get_customer_provided_info_by_id(
    request: Request,
    info_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("view_customer_provided_info")),
):
    """
    Lấy thông tin chi tiết một bản ghi theo ID.
    """
    return await handle_customer_provided_info.get_customer_provided_info(
        id=info_id,
        db=db,
        current_user=current_user,
    )


@router.post("")
@log_user_action("create_customer_provided_info")
async def create_customer_provided_info(
    info_data: CustomerProvidedInfoCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("create_customer_provided_info")),
):
    """
    Tạo mới thông tin khách hàng cung cấp.
    - User thường: tự động gán tenant_id từ current_user.
    - Super admin: có thể chỉ định tenant_id.
    """
    return await handle_customer_provided_info.create_customer_provided_info(
        info_data=info_data,
        db=db,
        current_user=current_user,
    )


@router.put("/{info_id}")
@log_user_action("update_customer_provided_info")
async def update_customer_provided_info(
    info_id: UUID,
    info_data: CustomerProvidedInfoUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("edit_customer_provided_info")),
):
    """
    Cập nhật thông tin khách hàng cung cấp.
    """
    return await handle_customer_provided_info.update_customer_provided_info(
        info_id=info_id,
        info_data=info_data,
        db=db,
        current_user=current_user,
    )


@router.delete("/{info_id}")
@log_user_action("delete_customer_provided_info")
async def delete_customer_provided_info(
    info_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("delete_customer_provided_info")),
):
    """
    Xóa (hard delete) thông tin khách hàng cung cấp.
    """
    return await handle_customer_provided_info.delete_customer_provided_info(
        info_id=info_id,
        db=db,
        current_user=current_user,
    )
