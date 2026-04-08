from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.database import get_db
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.core.security.permissions import has_permission
from app.db.models import User
from app.schemas.requests.customer import (
    CustomerCreateRequest,
    CustomerUpdateRequest,
)
from app.schemas.requests.customer_tag import CustomerTagUpdateRequest
from app.services.v1 import handle_customer


router = APIRouter(
    prefix="/customers",
    tags=["Customers"],
)


@router.get("")
async def get_customers(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của khách hàng"),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    search: Optional[str] = Query(
        None, description="Từ khóa tìm kiếm (tên / email / số điện thoại)"
    ),
    sort_by: Optional[str] = Query(
        None, description="Trường sắp xếp (name, created_at, updated_at, email, phone)"
    ),
    sort_order: str = Query("desc", description="Thứ tự sắp xếp (asc/desc)"),
    is_active: Optional[int] = Query(
        None, ge=0, le=1, description="Lọc theo trạng thái (0/1)"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("view_customers")),
):
    """
    Lấy danh sách khách hàng với phân trang, tìm kiếm.
    Nếu truyền `id` thì trả về luôn chi tiết 1 khách hàng.
    """
    return await handle_customer.get_customers(
        db=db,
        current_user=current_user,
        id=id,
        page=page,
        page_size=page_size,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        is_active=is_active,
    )


@router.get("/{customer_id}")
async def get_customer_by_id(
    request: Request,
    customer_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("view_customer_by_id")),
):
    """
    Lấy thông tin chi tiết một khách hàng theo ID.
    """
    return await handle_customer.get_customer_by_id(
        customer_id=customer_id,
        db=db,
        current_user=current_user,
    )


@router.post("")
@log_user_action("create_customer")
async def create_customer(
    customer_data: CustomerCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("create_customer")),
):
    """
    Tạo mới khách hàng.

    - User thường: tự động gán tenant_id từ current_user.
    - Super admin: có thể chỉ định tenant_id.
    - created_by luôn là current_user.
    """
    return await handle_customer.create_customer(
        customer_data=customer_data,
        db=db,
        current_user=current_user,
    )


@router.put("/{customer_id}")
@log_user_action("update_customer")
async def update_customer(
    customer_id: UUID,
    customer_data: CustomerUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("edit_customer")),
):
    """
    Cập nhật thông tin khách hàng.
    """
    return await handle_customer.update_customer(
        customer_id=customer_id,
        customer_data=customer_data,
        db=db,
        current_user=current_user,
    )


@router.delete("/{customer_id}")
@log_user_action("delete_customer")
async def delete_customer(
    customer_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("delete_customer")),
):
    """
    Xóa mềm khách hàng (is_active = 0).
    """
    return await handle_customer.soft_delete_customer(
        customer_id=customer_id,
        db=db,
        current_user=current_user,
    )


@router.get("/{customer_id}/tags")
async def get_customer_tags(
    request: Request,
    customer_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("view_customer_by_id")),
):
    """
    Lấy danh sách tag (type=CUSTOMER) gán cho một khách hàng.
    """
    return await handle_customer.get_customer_tags(
        customer_id=customer_id,
        db=db,
        current_user=current_user,
    )


@router.post("/{customer_id}/tags")
@log_user_action("add_customer_tags")
async def add_customer_tags(
    customer_id: UUID,
    payload: CustomerTagUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("edit_customer")),
):
    """
    Thêm (merge) danh sách tag vào khách hàng.
    Không xóa các tag đang có, chỉ bổ sung thêm.
    """
    return await handle_customer.add_tags_to_customer(
        customer_id=customer_id,
        payload=payload,
        db=db,
        current_user=current_user,
    )


@router.delete("/{customer_id}/tags")
@log_user_action("delete_customer_tags")
async def delete_customer_tags(
    customer_id: UUID,
    payload: CustomerTagUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _=Depends(has_permission("edit_customer")),
):
    """
    Gỡ 1 hoặc nhiều tag khỏi khách hàng.
    """
    return await handle_customer.remove_tags_from_customer(
        customer_id=customer_id,
        payload=payload,
        db=db,
        current_user=current_user,
    )

