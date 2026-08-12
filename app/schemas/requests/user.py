from pydantic import BaseModel, EmailStr, Field, StringConstraints
from typing import Annotated, Any, Optional
from uuid import UUID
from datetime import datetime


class UserWebphoneWriteFields(BaseModel):
    """Các trường softphone/SIP trên User (ghi — không trả secret ở response thường)."""
    webphone_enabled: bool | None = None
    sip_extension: str | None = Field(default=None, max_length=20)
    sip_username: str | None = Field(default=None, max_length=100)
    sip_password: str | None = Field(
        default=None,
        max_length=255,
        description="Mật khẩu SIP/3CX (lưu DB, chỉ admin chỉnh; không trả trong GET user)",
    )
    sip_domain: str | None = Field(default=None, max_length=255)
    sip_ws_server: str | None = Field(default=None, max_length=255)
    sip_port: int | None = None
    sip_protocol: str | None = Field(default=None, max_length=10)
    webphone_api_key: str | None = Field(
        default=None,
        max_length=255,
        description="API key webphone (secret — không trả trong GET user)",
    )
    webphone_process_id: str | None = Field(default=None, max_length=50)
    webphone_agent_id: str | None = Field(default=None, max_length=50)
    call_recording_enabled: bool | None = None
    call_log_enabled: bool | None = None


class UserWebphoneResponseFields(BaseModel):
    """Snapshot webphone không chứa secret (sip_password, webphone_api_key)."""
    webphone_enabled: bool = False
    sip_extension: str | None = None
    sip_username: str | None = None
    sip_domain: str | None = None
    sip_ws_server: str | None = None
    sip_port: int | None = None
    sip_protocol: str | None = None
    webphone_process_id: str | None = None
    webphone_agent_id: str | None = None
    call_recording_enabled: bool = True
    call_log_enabled: bool = True


class CreateUserRequest(UserWebphoneWriteFields):
    username: Annotated[str, StringConstraints(min_length=3, max_length=50)]
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=6)]
    fullname: str | None = None
    chat_id: int | None = None
    role_id: Optional[UUID] | None = None
    level_id: Optional[UUID] | None = None
    tenant_id: Optional[UUID] | None = None
    is_platform_admin: bool | None = Field(
        default=None,
        description="Chỉ platform admin mới được set khi tạo user",
    )
    meta_data: dict[str, Any] | None = Field(
        default=None,
        description=(
            "**Chỉ dùng cho messaging** (không map sang `role_id` / RBAC nội bộ). "
            "Toàn bộ key trong `meta_data` được merge vào payload Agent trong account tenant."
        ),
    )


class UpdateUserRequest(UserWebphoneWriteFields):
    username: Annotated[str, StringConstraints(min_length=3, max_length=50)] | None = None
    email: EmailStr | None = None
    password: Annotated[str, StringConstraints(min_length=6)] | None = None
    fullname: str | None = None
    chat_id: int | None = None
    role_id: Optional[UUID] | None = None
    level_id: Optional[UUID] | None = None
    is_active: int | None = None
    tenant_id: Optional[UUID] | None = None
    is_platform_admin: bool | None = Field(
        default=None,
        description="Chỉ platform admin mới được đổi cờ này",
    )
    meta_data: dict[str, Any] | None = Field(
        default=None,
        description="**Chỉ dùng cho Messaging Agent** trong account của tenant.",
    )


class ResponseUser(BaseModel):
    id: UUID
    username: str
    email: EmailStr | None = None
    fullname: str | None = None
    chat_id: int | None = None
    create_day: datetime | None = None
    is_active: int | None = 1
    role_id: Optional[UUID] | None = None
    level_id: Optional[UUID] | None = None
    tenant_id: Optional[UUID] | None = None
    is_platform_admin: bool = False
    role: str | None = None
    level: str | None = None
    order_level: int | None = None
    meta_data: dict[str, Any] | None = None
    webphone: UserWebphoneResponseFields | None = None
    webcall: dict[str, Any] | None = Field(
        default=None,
        description="Tóm tắt softphone (không secret). Chi tiết: GET /user/webcall",
    )
    messaging_synced: bool | None = None
    permissions: list[str] | None = None
