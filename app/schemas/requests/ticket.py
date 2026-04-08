from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime, timezone
from app.db.models import TicketStatus, TicketPriority


# Request Schemas
class TicketCreate(BaseModel):
    """Schema để tạo ticket mới"""
    title: str = Field(..., min_length=1, max_length=255, description="Tiêu đề ticket")
    description: Optional[str] = Field(None, description="Mô tả chi tiết ticket")
    priority: Optional[TicketPriority] = Field(TicketPriority.MEDIUM, description="Mức độ ưu tiên")
    template_id: Optional[UUID] = Field(None, description="ID của template (nếu có)")
    flow_id: Optional[UUID] = Field(None, description="ID của flow (nếu có)")
    sla_id: Optional[UUID] = Field(None, description="ID của SLA (nếu có)")
    assigned_to: Optional[UUID] = Field(None, description="ID của user được gán")
    tenant_id: Optional[UUID] = Field(None, description="ID của tenant (chỉ Super Admin)")
    
    # Extension data (optional)
    extension_data: Optional[Dict[str, Any]] = Field(None, description="Dữ liệu mở rộng theo template")
    
    # Tags (optional)
    tag_ids: Optional[List[UUID]] = Field(None, description="Danh sách ID của tags")
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "title": "Yêu cầu hỗ trợ kỹ thuật",
                "description": "Khách hàng báo lỗi không thể đăng nhập vào hệ thống",
                "priority": "high",
                "template_id": "019b8bea-d0b3-7d18-b717-228a2bab0c17",
                "assigned_to": "019b8bea-d0b3-7d18-b717-228a2bab0c18",
                "extension_data": {
                    "customer_name": "Nguyễn Văn A",
                    "phone": "0901234567"
                },
                "tag_ids": ["019b8bea-d0b3-7d18-b717-228a2bab0c19"]
            }
        }


class TicketUpdate(BaseModel):
    """Schema để cập nhật ticket"""
    title: Optional[str] = Field(None, min_length=1, max_length=255, description="Tiêu đề ticket")
    description: Optional[str] = Field(None, description="Mô tả chi tiết ticket")
    status: Optional[TicketStatus] = Field(None, description="Trạng thái ticket")
    priority: Optional[TicketPriority] = Field(None, description="Mức độ ưu tiên")
    assigned_to: Optional[UUID] = Field(None, description="ID của user được gán")
    sla_id: Optional[UUID] = Field(None, description="ID của SLA")
    template_id: Optional[UUID] = Field(None, description="ID của template")
    # Extension data (optional)
    extension_data: Optional[Dict[str, Any]] = Field(None, description="Dữ liệu mở rộng theo template")
    
    # Tags (optional)
    tag_ids: Optional[List[UUID]] = Field(None, description="Danh sách ID của tags")
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "status": "in_progress",
                "priority": "urgent",
                "assigned_to": "019b8bea-d0b3-7d18-b717-228a2bab0c18"
            }
        }


class TicketAssign(BaseModel):
    """Schema để gán ticket cho user"""
    assigned_to: UUID = Field(..., description="ID của user được gán")
    
    class Config:
        from_attributes = True


class TicketStatusUpdate(BaseModel):
    """Schema để cập nhật trạng thái ticket"""
    status: TicketStatus = Field(..., description="Trạng thái mới")
    note: Optional[str] = Field(None, description="Ghi chú về thay đổi trạng thái")
    
    class Config:
        from_attributes = True


# Response Schemas
class TicketResponse(BaseModel):
    """Schema response cho ticket"""
    id: UUID
    tenant_id: UUID
    code: str
    title: str
    description: Optional[str]
    status: TicketStatus
    priority: TicketPriority
    template_id: Optional[UUID]
    flow_id: Optional[UUID]
    sla_id: Optional[UUID]
    created_by: UUID
    assigned_to: Optional[UUID]
    created_at: datetime
    closed_at: Optional[datetime]
    
    # Relationship data (optional, loaded separately)
    template_name: Optional[str] = None
    flow_name: Optional[str] = None
    created_by_name: Optional[str] = None
    assigned_to_name: Optional[str] = None
    tags: Optional[List[Dict[str, Any]]] = None
    extension_data: Optional[Dict[str, Any]] = None
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "id": "019b8bea-d0b3-7d18-b717-228a2bab0c17",
                "tenant_id": "019b8bea-d0b3-7d18-b717-228a2bab0c16",
                "code": "TKT-2026-0001",
                "title": "Yêu cầu hỗ trợ kỹ thuật",
                "description": "Khách hàng báo lỗi không thể đăng nhập",
                "status": "open",
                "priority": "high",
                "created_by": "019b8bea-d0b3-7d18-b717-228a2bab0c18",
                "assigned_to": "019b8bea-d0b3-7d18-b717-228a2bab0c19",
                "created_at": "2026-01-13T02:34:00Z",
                "closed_at": None,
                "created_by_name": "Admin User",
                "assigned_to_name": "Support Agent",
                "tags": [{"id": "...", "name": "Technical"}]
            }
        }


class TicketListResponse(BaseModel):
    """Schema response cho danh sách tickets với pagination"""
    items: List[TicketResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    
    class Config:
        from_attributes = True
