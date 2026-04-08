from pydantic import BaseModel, Field, validator
from typing import Optional
from uuid import UUID
from datetime import datetime


class TicketFlowCreate(BaseModel):
    """Schema cho việc tạo TicketFlow mới"""
    name: str = Field(..., min_length=1, max_length=255, description="Tên flow")
    description: Optional[str] = Field(None, description="Mô tả flow")
    tenant_id: Optional[UUID] = Field(None, description="ID của tenant (chỉ Super Admin mới có thể chỉ định)")
    
    @validator('name')
    def validate_name(cls, v):
        """Validate tên flow"""
        if not v or not v.strip():
            raise ValueError("Tên flow không được để trống")
        return v.strip()
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "Flow xử lý ticket khẩn cấp",
                "description": "Flow xử lý các ticket có mức độ ưu tiên cao",
                "tenant_id": None
            }
        }


class TicketFlowUpdate(BaseModel):
    """Schema cho việc cập nhật TicketFlow"""
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Tên flow")
    description: Optional[str] = Field(None, description="Mô tả flow")
    
    @validator('name')
    def validate_name(cls, v):
        """Validate tên flow"""
        if v is not None:
            if not v.strip():
                raise ValueError("Tên flow không được để trống")
            return v.strip()
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "Flow xử lý ticket khẩn cấp (cập nhật)",
                "description": "Flow xử lý các ticket có mức độ ưu tiên cao - đã cập nhật"
            }
        }


class TicketFlowResponse(BaseModel):
    """Schema cho response TicketFlow"""
    id: UUID
    tenant_id: UUID
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "id": "019b8bea-d0b3-7d18-b717-228a2bab0c17",
                "tenant_id": "019b8bea-cb75-72da-8cb0-66359f310427",
                "name": "Flow xử lý ticket khẩn cấp",
                "description": "Flow xử lý các ticket có mức độ ưu tiên cao",
                "created_at": "2025-01-09T02:53:00Z",
                "updated_at": "2025-01-09T02:53:00Z"
            }
        }
