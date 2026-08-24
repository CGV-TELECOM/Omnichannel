from pydantic import BaseModel, Field
from typing import Any, Literal, Optional
from uuid import UUID  

class GroupBase(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    meta_data: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Dữ liệu mở rộng (JSON) để cấu hình đồng bộ Messaging Account (map theo tenant).\n\n"
            "Toàn bộ key/value trong `meta_data` có thể được dùng để cấu hình Chatbot hoặc build payload gọi Messaging Platform API "
            "`/platform/api/v1/accounts` (ví dụ: `locale`, `domain`, `support_email`, `features`, `limits`, `custom_attributes`, ...).\n\n"
            "Cấu hình Chatbot:\n"
            "- `default_responder` (str): 'bot' hoặc 'agent' (mặc định phản hồi khi có chat mới).\n"
            "- `chatbot_enabled` (bool): True hoặc False (bật/tắt tính năng chatbot).\n\n"
            "- `features` sẽ được sanitize theo whitelist để tránh messaging 500/duplicate account.\n"
            "- Tương thích ngược: nếu có `meta_data.chatwoot_account` (dict) thì ưu tiên dùng phần đó."
        ),
        json_schema_extra={
            "example": {
                "locale": "vi",
                "support_email": "support@example.com",
                "domain": "example.com",
                "features": {"inbound_emails": True, "reports": True},
                "custom_attributes": {"plan": "pro"},
            }
        },
    )
    graph_id: Optional[UUID] = None
    agent_id: Optional[UUID] = None
    graph_activated: Optional[int] = 0
    webcall_config: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Cấu hình WebCall / SIP theo tenant. "
            "Mặc định để trống; điền khi cấu hình thực tế."
        ),
        json_schema_extra={
            "example": {
                "enable_widget": True,
                "sip_only": True,
                "sip_domain": "",
                "domain_uuid": "",
                "hotlines": [],
                "ws_server": "",
                "sip_password": "",
                "api_key": "",
                "extension": "",
                "webhook_secret": "",
            }
        },
    )
    conversation_rating_enabled: Optional[bool] = Field(
        default=None,
        description=(
            "Bật/tắt gửi link đánh giá CSAT OmniHub khi resolve conversation "
            "(kênh ngoài web widget). Mặc định true."
        ),
    )

class TenantCreate(GroupBase):
    pass

class TenantUpdate(GroupBase):
    pass

class TenantResponse(GroupBase):
    id: Optional[UUID] = None  # sửa lại kiểu đúng
    is_active: Optional[int] = None

    class Config:
        from_attributes = True  # để serialize từ ORM


class TenantOwnSettingsUpdate(BaseModel):
    """PATCH /tenants/me/settings — chỉ field vận hành, không đổi tên/provision."""

    conversation_rating_enabled: Optional[bool] = Field(
        default=None,
        description="Bật/tắt gửi link đánh giá CSAT khi resolve (kênh ngoài web widget).",
    )
    chatbot_enabled: Optional[bool] = Field(
        default=None,
        description="Bật/tắt chatbot OmniHub (lưu meta_data, không sync Chatwoot).",
    )
    default_responder: Optional[Literal["bot", "agent"]] = Field(
        default=None,
        description="Người trả lời mặc định khi có chat mới: bot hoặc agent.",
    )


class TenantOwnSettingsResponse(BaseModel):
    conversation_rating_enabled: bool
    chatbot_enabled: bool
    default_responder: Literal["bot", "agent"]
