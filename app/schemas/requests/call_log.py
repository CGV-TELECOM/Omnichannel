from pydantic import BaseModel, Field, field_validator
from typing import Optional, Any, Dict, Union
from uuid import UUID
from datetime import datetime


class CallLogBase(BaseModel):
    sip_call_id: UUID = Field(..., description="UUID định danh cuộc gọi từ tổng đài (khóa map)")
    customer_id: Optional[UUID] = Field(None, description="ID của khách hàng")
    ticket_id: Optional[UUID] = Field(None, description="ID của ticket liên kết")
    user_id: Optional[UUID] = Field(None, description="ID của agent thực hiện/nhận cuộc gọi")
    direction: str = Field("outbound", max_length=20, description="inbound | outbound | internal")
    phone_number: str = Field(..., max_length=20, description="Số điện thoại khách hàng")
    from_number: Optional[str] = Field(None, max_length=30)
    to_number: Optional[str] = Field(None, max_length=30)
    hotline: Optional[str] = Field(None, max_length=30)
    status: Optional[str] = Field(None, max_length=50, description="created|ringing|answered|ended|missed|...")
    source: Optional[str] = Field("web", max_length=20)
    started_at: Optional[datetime] = None
    answered_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration: Optional[int] = Field(0, description="Thời lượng (giây)")
    billsec: Optional[int] = Field(0, description="Thời lượng tính cước (giây)")
    recording_url: Optional[str] = Field(None, max_length=512)
    provider_call_id: Optional[UUID] = Field(None, description="call_id phía PBX (phụ)")
    meta_data: Optional[Dict[str, Any]] = None

    @field_validator("sip_call_id", mode="before")
    @classmethod
    def coerce_sip_call_id(cls, v):
        if isinstance(v, UUID):
            return v
        return UUID(str(v))


class CallLogCreate(CallLogBase):
    tenant_id: Optional[UUID] = Field(None, description="Tenant (Super Admin); mặc định theo user")


class CallLogUpdate(BaseModel):
    customer_id: Optional[UUID] = None
    ticket_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    status: Optional[str] = None
    from_number: Optional[str] = None
    to_number: Optional[str] = None
    hotline: Optional[str] = None
    started_at: Optional[datetime] = None
    answered_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration: Optional[int] = None
    billsec: Optional[int] = None
    recording_url: Optional[str] = None
    provider_call_id: Optional[UUID] = None
    meta_data: Optional[Dict[str, Any]] = None


class CallLogResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    sip_call_id: UUID
    provider_call_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    ticket_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    direction: str
    phone_number: str
    from_number: Optional[str] = None
    to_number: Optional[str] = None
    hotline: Optional[str] = None
    status: Optional[str] = None
    source: Optional[str] = None
    started_at: Optional[datetime] = None
    answered_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration: Optional[int] = 0
    billsec: Optional[int] = 0
    recording_url: Optional[str] = None
    meta_data: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CallLogEventResponse(BaseModel):
    id: UUID
    call_log_id: UUID
    tenant_id: UUID
    sip_call_id: UUID
    provider_call_id: Optional[UUID] = None
    state: str
    application: Optional[str] = None
    event_at: Optional[datetime] = None
    received_at: datetime
    payload: Dict[str, Any]
    idempotency_key: Optional[str] = None

    class Config:
        from_attributes = True
