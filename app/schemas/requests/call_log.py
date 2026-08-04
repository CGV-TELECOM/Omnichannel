from pydantic import BaseModel, Field
from typing import Optional, Any, Dict
from uuid import UUID
from datetime import datetime

class CallLogBase(BaseModel):
    sip_call_id: str = Field(..., min_length=1, max_length=255, description="ID định danh cuộc gọi từ tổng đài")
    customer_id: Optional[UUID] = Field(None, description="ID của khách hàng")
    ticket_id: Optional[UUID] = Field(None, description="ID của ticket liên kết")
    user_id: Optional[UUID] = Field(None, description="ID của agent thực hiện/nhận cuộc gọi")
    direction: str = Field("outbound", max_length=20, description="Chiều cuộc gọi: inbound hoặc outbound")
    phone_number: str = Field(..., max_length=20, description="Số điện thoại khách hàng")
    status: Optional[str] = Field(None, max_length=50, description="Trạng thái cuộc gọi (ringing, answered, ended, missed, v.v.)")
    started_at: Optional[datetime] = Field(None, description="Thời gian bắt đầu cuộc gọi")
    ended_at: Optional[datetime] = Field(None, description="Thời gian kết thúc cuộc gọi")
    duration: Optional[int] = Field(0, description="Thời lượng cuộc gọi (giây)")
    recording_url: Optional[str] = Field(None, max_length=512, description="Đường dẫn file ghi âm cuộc gọi")
    meta_data: Optional[Dict[str, Any]] = Field(None, description="Metadata bổ sung")

class CallLogCreate(CallLogBase):
    tenant_id: Optional[UUID] = Field(None, description="ID của tenant (nếu không truyền sẽ lấy theo tenant của user hiện tại)")

class CallLogUpdate(BaseModel):
    customer_id: Optional[UUID] = Field(None, description="ID của khách hàng")
    ticket_id: Optional[UUID] = Field(None, description="ID của ticket liên kết")
    user_id: Optional[UUID] = Field(None, description="ID của agent thực hiện/nhận cuộc gọi")
    status: Optional[str] = Field(None, max_length=50, description="Trạng thái cuộc gọi")
    started_at: Optional[datetime] = Field(None, description="Thời gian bắt đầu cuộc gọi")
    ended_at: Optional[datetime] = Field(None, description="Thời gian kết thúc cuộc gọi")
    duration: Optional[int] = Field(None, description="Thời lượng cuộc gọi (giây)")
    recording_url: Optional[str] = Field(None, max_length=512, description="Đường dẫn file ghi âm cuộc gọi")
    meta_data: Optional[Dict[str, Any]] = Field(None, description="Metadata bổ sung")


class CallLogResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    sip_call_id: str
    customer_id: Optional[UUID] = None
    ticket_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    direction: str
    phone_number: str
    status: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration: Optional[int] = 0
    recording_url: Optional[str] = None
    meta_data: Optional[Dict[str, Any]] = None
    created_at: datetime

    class Config:
        from_attributes = True
