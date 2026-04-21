from pydantic import BaseModel, EmailStr, Field, StringConstraints
from typing import Annotated, Any
from uuid import UUID
from typing import Optional


class CreateUserRequest(BaseModel):
    username: Annotated[str, StringConstraints(min_length=3, max_length=50)]
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=6)]
    fullname: str | None = None
    chat_id: int | None = None
    role_id: Optional[UUID] | None = None
    level_id: Optional[UUID] | None = None
    tenant_id: Optional[UUID] | None = None
    meta_data: dict[str, Any] | None = Field(
        default=None,
        description=(
            "**Chỉ dùng cho Chatwoot** (không map sang `role_id` / RBAC nội bộ). "
            "Toàn bộ key trong `meta_data` được merge vào payload Agent trong account tenant "
            "(Application API `/accounts/{id}/agents/...`).\n\n"
            "- `meta_data.role` = role **trên Chatwoot** (vd `agent`, `administrator`), khác hẳn `role_id` ở body.\n"
            "- `meta_data.password` = đổi mật khẩu **agent trên Chatwoot** (không lưu vào DB snapshot).\n"
            "- Có thể dùng `chatwoot_agent` (dict) để gom nhóm; nếu cùng key với root (vd `role`), **root ghi đè**.\n"
            "- Gọi endpoint Platform User (`/chatwoot/users/...`) thì dùng luồng riêng trong `handle_chatwoot`."
        ),
        json_schema_extra={
            "example": {
                "role": "administrator",
                "display_name": "Nguyễn Xuân Mạnh",
                "password": "NewStrongPassword#1",
                "chatwoot_agent": {
                    "availability_status": "online",
                    "custom_attributes": {"team": "CSKH"},
                },
            }
        },
    )

class UpdateUserRequest(BaseModel):
    username: Annotated[str, StringConstraints(min_length=3, max_length=50)] | None = None
    email: EmailStr | None = None
    password: Annotated[str, StringConstraints(min_length=6)] | None = None
    fullname: str | None = None
    chat_id: int | None = None
    role_id: Optional[UUID] | None = None
    level_id: Optional[UUID] | None = None
    is_active: int | None = None
    tenant_id: Optional[UUID] | None = None
    meta_data: dict[str, Any] | None = Field(
        default=None,
        description=(
            "**Chỉ dùng cho Chatwoot Agent** trong account của tenant (không đổi `role_id` nội bộ). "
            "Chỉ cần gửi `meta_data` (vd `role`, `password`) là hệ thống sẽ **PATCH agent lên Chatwoot**, "
            "không bắt buộc đổi fullname/email cùng lúc.\n\n"
            "- Merge với meta_data cũ (deep-merge cho `chatwoot_agent`).\n"
            "- `meta_data.role` / `password` áp dụng cho Chatwoot, không phải mật khẩu đăng nhập omnichannel trừ khi bạn gửi `password` ở root body."
        ),
        json_schema_extra={
            "example": {
                "role": "administrator",
                "password": "RotatePwd#2026",
                "custom_attributes": {"region": "HN"},
                "chatwoot_agent": {"availability_status": "busy"},
            }
        },  
    )

class ResponseUser(BaseModel):
    id: UUID
    username: str
    email: EmailStr
    fullname: str | None = None
    role_id: Optional[UUID] | None = None
    level_id: Optional[UUID] | None = None
    tenant_id: Optional[UUID] | None = None
    meta_data: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Lưu cấu hình client + snapshot Chatwoot.\n\n"
            "- `chatwoot_agent`: snapshot payload đã gửi sang Chatwoot (không lưu password).\n"
            "- Các key khác (vd `role` ở root meta_data) do client gửi; không phải RBAC nội bộ."
        ),
    )

