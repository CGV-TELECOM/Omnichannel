from typing import Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field

class CustomerProvidedInfoBase(BaseModel):
    name: Optional[str] = Field(None, max_length=255, description="Tên khách hàng cung cấp")
    email: Optional[EmailStr] = Field(None, description="Email khách hàng")
    phone: Optional[str] = Field(None, max_length=20, description="Số điện thoại")
    description: Optional[str] = Field(None, description="Mô tả hoặc thông tin khách hàng cung cấp")
    tenant_id: Optional[UUID] = Field(None, description="Tenant ID của thông tin này")

class CustomerProvidedInfoCreate(CustomerProvidedInfoBase):
    pass

class CustomerProvidedInfoUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)
    description: Optional[str] = None
    tenant_id: Optional[UUID] = None

class CustomerProvidedInfoResponse(BaseModel):
    id: UUID
    tenant_id: Optional[UUID] = None
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
