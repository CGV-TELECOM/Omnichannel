from pydantic import BaseModel, Field, validator
from typing import Optional
from uuid import UUID
from datetime import datetime
from app.db.models import FlowInstanceStatus


class TicketFlowInstanceCreate(BaseModel):
    """Schema cho việc tạo TicketFlowInstance mới"""
    ticket_id: UUID = Field(..., description="ID của ticket")
    flow_id: UUID = Field(..., description="ID của flow")
    current_step_id: Optional[UUID] = Field(None, description="ID của step hiện tại")
    status: Optional[FlowInstanceStatus] = Field(FlowInstanceStatus.PENDING, description="Trạng thái của flow instance")
    tenant_id: Optional[UUID] = Field(None, description="ID của tenant (chỉ Super Admin mới có thể chỉ định)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "ticket_id": "019b8bea-d0b3-7d18-b717-228a2bab0c17",
                "flow_id": "019b8bea-cb75-72da-8cb0-66359f310427",
                "current_step_id": None,
                "status": "pending",
                "tenant_id": None
            }
        }


class TicketFlowInstanceUpdate(BaseModel):
    """Schema cho việc cập nhật TicketFlowInstance"""
    current_step_id: Optional[UUID] = Field(None, description="ID của step hiện tại")
    status: Optional[FlowInstanceStatus] = Field(None, description="Trạng thái của flow instance")
    finished_at: Optional[datetime] = Field(None, description="Thời gian hoàn thành")
    
    class Config:
        json_schema_extra = {
            "example": {
                "current_step_id": "019b8bea-d0b3-7d18-b717-228a2bab0c18",
                "status": "running",
                "finished_at": None
            }
        }


class TicketFlowInstanceResponse(BaseModel):
    """Schema cho response TicketFlowInstance"""
    id: UUID
    ticket_id: UUID
    flow_id: UUID
    current_step_id: Optional[UUID] = None
    status: FlowInstanceStatus
    started_at: datetime
    finished_at: Optional[datetime] = None
    tenant_id: Optional[UUID] = None
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "id": "019b8bea-d0b3-7d18-b717-228a2bab0c19",
                "ticket_id": "019b8bea-d0b3-7d18-b717-228a2bab0c17",
                "flow_id": "019b8bea-cb75-72da-8cb0-66359f310427",
                "current_step_id": "019b8bea-d0b3-7d18-b717-228a2bab0c18",
                "status": "running",
                "started_at": "2025-01-09T02:53:00Z",
                "finished_at": None,
                "tenant_id": "019b8bea-cb75-72da-8cb0-66359f310427"
            }
        }
