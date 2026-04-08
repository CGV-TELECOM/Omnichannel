from pydantic import BaseModel, Field, validator
from typing import Optional
from uuid import UUID
from datetime import datetime


class TicketFlowStepCreate(BaseModel):
    """Schema cho việc tạo TicketFlowStep mới"""
    flow_id: UUID = Field(..., description="ID của flow")
    step_name: str = Field(..., min_length=1, max_length=255, description="Tên của step")
    step_order: int = Field(..., ge=1, description="Thứ tự của step (bắt đầu từ 1)")
    assignee_user_id: Optional[UUID] = Field(None, description="ID của user được gán")
    assignee_group_id: Optional[UUID] = Field(None, description="ID của group được gán")
    
    @validator('step_name')
    def validate_step_name(cls, v):
        """Validate tên step"""
        if not v or not v.strip():
            raise ValueError("Tên step không được để trống")
        return v.strip()
    
    @validator('assignee_user_id', 'assignee_group_id')
    def validate_assignee(cls, v, values):
        """Validate assignee - phải có ít nhất một trong hai (user hoặc group) hoặc không có cả hai"""
        # Validation này sẽ được xử lý trong service
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "flow_id": "019b8bea-cb75-72da-8cb0-66359f310427",
                "step_name": "Bước 1: Tiếp nhận ticket",
                "step_order": 1,
                "assignee_user_id": None,
                "assignee_group_id": "019b8bea-d0b3-7d18-b717-228a2bab0c17"
            }
        }


class TicketFlowStepUpdate(BaseModel):
    """Schema cho việc cập nhật TicketFlowStep"""
    step_name: Optional[str] = Field(None, min_length=1, max_length=255, description="Tên của step")
    step_order: Optional[int] = Field(None, ge=1, description="Thứ tự của step")
    assignee_user_id: Optional[UUID] = Field(None, description="ID của user được gán")
    assignee_group_id: Optional[UUID] = Field(None, description="ID của group được gán")
    
    @validator('step_name')
    def validate_step_name(cls, v):
        """Validate tên step"""
        if v is not None:
            if not v.strip():
                raise ValueError("Tên step không được để trống")
            return v.strip()
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "step_name": "Bước 1: Tiếp nhận ticket (cập nhật)",
                "step_order": 1,
                "assignee_user_id": "019b8bea-d0b3-7d18-b717-228a2bab0c18",
                "assignee_group_id": None
            }
        }


class TicketFlowStepResponse(BaseModel):
    """Schema cho response TicketFlowStep"""
    id: UUID
    flow_id: UUID
    step_name: str
    step_order: int
    assignee_user_id: Optional[UUID] = None
    assignee_group_id: Optional[UUID] = None
    created_at: datetime
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "id": "019b8bea-d0b3-7d18-b717-228a2bab0c19",
                "flow_id": "019b8bea-cb75-72da-8cb0-66359f310427",
                "step_name": "Bước 1: Tiếp nhận ticket",
                "step_order": 1,
                "assignee_user_id": None,
                "assignee_group_id": "019b8bea-d0b3-7d18-b717-228a2bab0c17",
                "created_at": "2025-01-09T02:53:00Z"
            }
        }
