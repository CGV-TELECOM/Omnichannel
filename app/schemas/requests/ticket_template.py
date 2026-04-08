from pydantic import BaseModel, Field
from typing import Optional, Any, Dict
from uuid import UUID
from datetime import datetime

class TicketTemplateBase(BaseModel):
    """Base schema cho TicketTemplate"""
    name: str = Field(..., min_length=1, max_length=255, description="Tên template")
    description: Optional[str] = Field(None, description="Mô tả template")
    flow_id: Optional[UUID] = Field(None, description="ID của flow")
    sla_id: Optional[UUID] = Field(None, description="ID của SLA")
    extension_schema: Optional[Dict[str, Any]] = Field(None, description="Schema mở rộng (JSON)")
    is_active: Optional[bool] = Field(True, description="Trạng thái hoạt động")
    tenant_id: Optional[UUID] = Field(None, description="ID của tenant (chỉ Super Admin mới có thể chỉ định)")

class TicketTemplateCreate(TicketTemplateBase):
    """Schema để tạo TicketTemplate mới"""
    pass

class TicketTemplateUpdate(BaseModel):
    """Schema để cập nhật TicketTemplate"""
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Tên template")
    description: Optional[str] = Field(None, description="Mô tả template")
    flow_id: Optional[UUID] = Field(None, description="ID của flow")
    sla_id: Optional[UUID] = Field(None, description="ID của SLA")
    extension_schema: Optional[Dict[str, Any]] = Field(None, description="Schema mở rộng (JSON)")
    is_active: Optional[bool] = Field(None, description="Trạng thái hoạt động")

class TicketTemplateResponse(BaseModel):
    """Schema cho response của TicketTemplate"""
    id: UUID
    tenant_id: UUID
    name: str
    description: Optional[str] = None
    flow_id: Optional[UUID] = None
    sla_id: Optional[UUID] = None
    extension_schema: Optional[Dict[str, Any]] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
