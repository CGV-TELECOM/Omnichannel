from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class CustomerCreateRequest(BaseModel):
    """
    Payload tạo mới Customer.
    - tenant_id: chỉ cho phép set khi là super admin, user thường sẽ tự động lấy từ current_user
    - tag_ids: danh sách tag thuộc type=CUSTOMER để gán cho customer (optional)
    """

    name: str = Field(..., min_length=1, max_length=255, description="Tên khách hàng")
    phone: Optional[str] = Field(None, max_length=20, description="Số điện thoại")
    email: Optional[EmailStr] = Field(None, description="Email khách hàng")
    tenant_id: Optional[UUID] = Field(
        None, description="Tenant của customer (chỉ super admin mới được phép thay đổi)"
    )
    meta_data: Optional[dict] = Field(
        None, description="Thông tin mở rộng của khách hàng (JSON)"
    )
    tag_ids: Optional[List[UUID]] = Field(
        default=None,
        description="Danh sách ID của các tag (type=CUSTOMER) gán cho customer",
    )


class CustomerUpdateRequest(BaseModel):
    """
    Payload cập nhật Customer.
    - created_by không được phép chỉnh sửa.
    """

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[EmailStr] = None
    tenant_id: Optional[UUID] = Field(
        None, description="Tenant mới của customer (chỉ super admin mới được phép thay đổi)"
    )
    meta_data: Optional[dict] = None
    is_active: Optional[int] = Field(
        None, ge=0, le=1, description="Trạng thái hoạt động của customer"
    )
    tag_ids: Optional[List[UUID]] = Field(
        default=None,
        description="Danh sách ID tag (type=CUSTOMER) để cập nhật quan hệ tags của customer",
    )


class CustomerResponse(BaseModel):
    """
    Schema response cơ bản cho Customer.
    """

    id: UUID
    name: str
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    tenant_id: Optional[UUID] = None
    created_by: Optional[UUID] = None
    created_at: str
    updated_at: Optional[str] = None
    meta_data: Optional[dict] = None
    is_active: int
    tag_ids: Optional[list[UUID]] = None

    class Config:
        from_attributes = True

