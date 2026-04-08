from pydantic import BaseModel, Field
from typing import Optional, Any, Dict
from uuid import UUID

class TicketExtensionBase(BaseModel):
    """Base schema cho TicketExtension"""
    ticket_id: UUID = Field(..., description="ID của ticket")
    data: Optional[Dict[str, Any]] = Field(None, description="Dữ liệu mở rộng (JSON) - Lưu các trường custom, dynamic fields")

class TicketExtensionCreate(TicketExtensionBase):
    """Schema để tạo hoặc cập nhật TicketExtension"""
    pass

class TicketExtensionUpdate(BaseModel):
    """Schema để cập nhật TicketExtension"""
    data: Optional[Dict[str, Any]] = Field(None, description="Dữ liệu mở rộng (JSON)")

class TicketExtensionResponse(BaseModel):
    """Schema cho response của TicketExtension"""
    ticket_id: UUID
    data: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True
