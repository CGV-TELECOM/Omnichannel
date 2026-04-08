from pydantic import BaseModel, Field, validator
from typing import Optional, Any, Dict
from uuid import UUID
from datetime import datetime

class TicketEventBase(BaseModel):
    """Base schema cho TicketEvent"""
    ticket_id: UUID = Field(..., description="ID của ticket")
    event_type: str = Field(..., min_length=1, max_length=50, description="Loại sự kiện (CREATED, UPDATED, REOPENED, CLOSED, ASSIGNED, COMMENTED, etc.)")
    payload: Optional[Dict[str, Any]] = Field(None, description="Dữ liệu chi tiết của sự kiện (JSON)")
    actor_id: Optional[str] = Field(None, max_length=100, description="ID của actor dạng chuỗi (user_id, 'system', 'api', etc.) - Mặc định là user hiện tại")
    tenant_id: Optional[UUID] = Field(None, description="ID của tenant (chỉ Super Admin mới có thể chỉ định)")

class TicketEventCreate(TicketEventBase):
    """Schema để tạo TicketEvent mới"""
    pass

class TicketEventUpdate(BaseModel):
    """Schema để cập nhật TicketEvent"""
    event_type: Optional[str] = Field(None, min_length=1, max_length=50, description="Loại sự kiện (CREATED, UPDATED, REOPENED, CLOSED, etc.)")
    payload: Optional[Dict[str, Any]] = Field(None, description="Dữ liệu chi tiết của sự kiện (JSON)")

class TicketEventResponse(BaseModel):
    """Schema cho response của TicketEvent"""
    id: UUID
    ticket_id: UUID
    event_type: str
    payload: Optional[Dict[str, Any]] = None
    actor_type: Optional[str] = None  # Tên role của user
    actor_id: Optional[str] = None  # String để linh hoạt
    created_at: datetime
    tenant_id: Optional[UUID] = None

    class Config:
        from_attributes = True

class TicketEventFilter(BaseModel):
    """Schema để filter TicketEvent"""
    ticket_id: Optional[UUID] = Field(None, description="Lọc theo ticket_id")
    event_type: Optional[str] = Field(None, description="Lọc theo loại sự kiện")
    actor_type: Optional[str] = Field(None, description="Lọc theo role của actor")
    actor_id: Optional[str] = Field(None, description="Lọc theo actor_id (string)")
    tenant_id: Optional[UUID] = Field(None, description="Lọc theo tenant_id")
    from_date: Optional[datetime] = Field(None, description="Lọc từ ngày")
    to_date: Optional[datetime] = Field(None, description="Lọc đến ngày")
