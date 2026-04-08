from pydantic import BaseModel, Field
from typing import Optional, Any, Dict
from uuid import UUID
from datetime import datetime

class TicketContextBase(BaseModel):
    """Base schema cho TicketContext"""
    ticket_id: UUID = Field(..., description="ID của ticket")
    context_type: str = Field(..., min_length=1, max_length=50, description="Loại context (customer, product, order, call, email, etc.)")
    context_id: str = Field(..., min_length=1, max_length=100, description="ID của context (customer_id, product_id, order_id, etc.)")
    source_type: Optional[str] = Field(None, max_length=50, description="Nguồn của context (crm, erp, call_system, email_system, etc.)")
    context_metadata: Optional[Dict[str, Any]] = Field(None, description="Metadata bổ sung (JSON)")
    tenant_id: Optional[UUID] = Field(None, description="ID của tenant (chỉ Super Admin mới có thể chỉ định)")

class TicketContextCreate(TicketContextBase):
    """Schema để tạo TicketContext mới"""
    pass

class TicketContextUpdate(BaseModel):
    """Schema để cập nhật TicketContext"""
    context_type: Optional[str] = Field(None, min_length=1, max_length=50, description="Loại context")
    context_id: Optional[str] = Field(None, min_length=1, max_length=100, description="ID của context")
    source_type: Optional[str] = Field(None, max_length=50, description="Nguồn của context")
    context_metadata: Optional[Dict[str, Any]] = Field(None, description="Metadata bổ sung (JSON)")

class TicketContextResponse(BaseModel):
    """Schema cho response của TicketContext"""
    id: UUID
    ticket_id: UUID
    context_type: str
    context_id: str
    source_type: Optional[str] = None
    context_metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
    tenant_id: Optional[UUID] = None

    class Config:
        from_attributes = True
