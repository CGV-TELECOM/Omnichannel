from pydantic import BaseModel, Field
from typing import Any, Literal, Optional
from uuid import UUID
from datetime import datetime


class TenantKgAgentInput(BaseModel):
    """Một agent KG Core thuộc tenant."""

    key: str = Field(default="default", max_length=64)
    kg_agent_id: UUID = Field(..., description="UUID agent trên KG Core")
    graph_id: Optional[UUID] = Field(
        default=None,
        description="Graph override; null → dùng tenant.graph_id",
    )
    label: Optional[str] = Field(default=None, max_length=128)
    is_default: bool = Field(default=False)
    is_active: bool = Field(default=True)


class TenantKgAgentResponse(TenantKgAgentInput):
    id: UUID
    tenant_id: UUID
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


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
            "- `chatbot_enabled` (bool): True hoặc False (bật/tắt tính năng chatbot).\n"
            "- `messaging_bots` (list): danh sách bot theo `agent_uuid` (UUID map agent nội bộ). "
            "Mặc định `[]` — tenant không dùng bot vẫn có field này; thêm phần tử khi bật AI Bot.\n\n"
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
                "chatbot_enabled": True,
                "default_responder": "agent",
                "messaging_bots": [],
            }
        },
    )
    graph_id: Optional[UUID] = Field(
        default=None,
        description="Graph KG mặc định của tenant (có thể override từng agent trong kg_agents).",
    )
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
            "(mọi kênh messaging, gồm live chat). Mặc định true."
        ),
    )


class TenantCreate(GroupBase):
    kg_agents: Optional[list[TenantKgAgentInput]] = Field(
        default=None,
        description="Danh sách agent KG Core gắn tenant.",
    )


class TenantUpdate(GroupBase):
    kg_agents: Optional[list[TenantKgAgentInput]] = Field(
        default=None,
        description="Gửi full list để thay thế agent KG của tenant.",
    )


class TenantResponse(GroupBase):
    id: Optional[UUID] = None
    is_active: Optional[int] = None
    kg_agents: list[TenantKgAgentResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True


class MessagingBotEntry(BaseModel):
    """Một agent messaging được đánh dấu là AI Bot của tenant (lưu UUID map)."""

    key: str = Field(
        default="default",
        max_length=64,
        description="Key logic bot (default, sales_bot, ...).",
    )
    agent_uuid: UUID = Field(
        ...,
        description="UUID agent nội bộ (từ GET messaging agents / chatwoot_legacy_map).",
    )
    is_default: bool = Field(
        default=False,
        description="Bot dùng khi auto-assign / assign-bot. Chỉ một phần tử nên true.",
    )
    label: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Nhãn hiển thị (tuỳ chọn).",
    )
    tenant_kg_agent_id: Optional[UUID] = Field(
        default=None,
        description="FK tenant_kg_agents.id — KG agent dùng khi bot này reply.",
    )


class TenantKgAgentsReplaceBody(BaseModel):
    """PUT /tenants/{tenant_id}/kg-agents — thay toàn bộ list."""

    kg_agents: list[TenantKgAgentInput] = Field(default_factory=list)


class TenantOwnSettingsUpdate(BaseModel):
    """PATCH /tenants/me/settings — chỉ field vận hành, không đổi tên/provision."""

    conversation_rating_enabled: Optional[bool] = Field(
        default=None,
        description="Bật/tắt gửi link đánh giá CSAT khi resolve (mọi kênh, gồm live chat).",
    )
    chatbot_enabled: Optional[bool] = Field(
        default=None,
        description="Bật/tắt chatbot OmniHub (lưu meta_data, không sync Chatwoot).",
    )
    default_responder: Optional[Literal["bot", "agent"]] = Field(
        default=None,
        description=(
            "Người trả lời mặc định khi có chat mới: bot hoặc agent. "
            "Chỉ có hiệu lực auto-assign khi messaging_bots có ít nhất 1 bot is_default."
        ),
    )
    messaging_bots: Optional[list[MessagingBotEntry]] = Field(
        default=None,
        description=(
            "Danh sách agent được phép coi là bot (UUID map). "
            "Gửi full list để thay thế; `[]` = tenant không dùng AI Bot. "
            "Thêm nhiều bot sau này bằng cách bổ sung phần tử (một is_default)."
        ),
    )


class TenantOwnSettingsResponse(BaseModel):
    conversation_rating_enabled: bool
    chatbot_enabled: bool
    default_responder: Literal["bot", "agent"]
    messaging_bots: list[MessagingBotEntry] = Field(default_factory=list)
