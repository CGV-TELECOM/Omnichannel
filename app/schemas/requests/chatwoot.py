from __future__ import annotations

"""
Request body trùng với schema Chatwoot:
- Account create/update: account_create_update_payload + features (Rails permit)
- Agent create/update: application_swagger agent_create_payload / agent_update_payload
- AgentBot create/update: platform `platform_agent_bot_create_update_payload` (POST/PATCH /platform/api/v1/agent_bots)

Tham chiếu:
https://github.com/chatwoot/chatwoot/blob/develop/swagger/tag_groups/platform_swagger.json
https://github.com/chatwoot/chatwoot/blob/develop/swagger/tag_groups/application_swagger.json
https://developers.chatwoot.com/api-reference/agentbots/list-all-agentbots
"""

from typing import Any, Literal, List, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatwootProvisionAccountBody(BaseModel):
    """
    POST /platform/api/v1/accounts — body giống `account_create_update_payload`.
    Thêm `features` (object) vì Platform::Api::V1::AccountsController permit `features: {}`
    dù swagger công khai đôi khi không liệt kê.
    """

    model_config = ConfigDict(extra="allow")

    tenant_id: UUID = Field(description="UUID tenant trên contact-center (không gửi sang Chatwoot)")
    name: str = Field(min_length=1, description="Name of the account")
    locale: str | None = Field(default=None, description="The locale of the account (e.g. en, vi)")
    domain: str | None = Field(default=None, description="The domain of the account (max 100 chars on Chatwoot)")
    support_email: str | None = Field(default=None, description="The support email of the account")
    status: Literal["active", "suspended"] | None = Field(
        default=None, description="The status of the account"
    )
    limits: dict[str, Any] | None = Field(default=None, description="The limits of the account")
    custom_attributes: dict[str, Any] | None = Field(
        default=None, description="The custom attributes of the account"
    )
    features: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Feature flags (Platform API). Key không nằm trong whitelist server sẽ **bỏ** trước khi gửi Chatwoot "
            "(tránh 500 và tránh phải POST lại — POST lại dễ tạo duplicate account)."
        ),
    )


class ChatwootUpdateAccountBody(BaseModel):
    """PATCH /platform/api/v1/accounts/{account_id} — cùng shape với create; mọi trường tùy chọn."""

    model_config = ConfigDict(extra="allow")

    name: str | None = Field(default=None, min_length=1, description="Name of the account")
    locale: str | None = None
    domain: str | None = None
    support_email: str | None = None
    status: Literal["active", "suspended"] | None = None
    limits: dict[str, Any] | None = None
    custom_attributes: dict[str, Any] | None = None
    features: dict[str, Any] | None = None


class ChatwootAgentCreateBody(BaseModel):
    """POST /api/v1/accounts/{account_id}/agents — agent_create_payload."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(description="Full Name of the agent")
    email: str = Field(description="Email of the Agent")
    role: Literal["agent", "administrator"] = Field(
        description="Whether its administrator or agent",
    )
    availability_status: Literal["available", "busy", "offline"] | None = Field(
        default=None,
        description="The availability setting of the agent",
    )
    auto_offline: bool | None = Field(
        default=None,
        description="Whether the availability status of agent is configured to go offline automatically when away",
    )


class ChatwootAgentUpdateBody(BaseModel):
    """PATCH /api/v1/accounts/{account_id}/agents/{id} — agent_update_payload (role required)."""

    model_config = ConfigDict(extra="allow")

    role: Literal["agent", "administrator"]
    availability_status: Literal["available", "busy", "offline"] | None = None
    auto_offline: bool | None = None


class ChatwootAgentBotCreateBody(BaseModel):
    """
    POST /platform/api/v1/agent_bots — đủ field theo `platform_agent_bot_create_update_payload`
    và `params.permit` trong `Platform::Api::V1::AgentBotsController` (name, description, account_id,
    outgoing_url, avatar, avatar_url).

    - **avatar** (binary): swagger mô tả multipart/form-data; API JSON của contact-center **không** gửi
      được file — dùng `avatar_url` hoặc gọi trực tiếp Chatwoot multipart nếu cần upload file.
    - **account_id**: với `POST /chatwoot/tenants/{tenant_id}/agent-bots`, server **luôn ghi đè**
      bằng Chatwoot account đã map (an toàn đa-tenant).
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, description="The name of the agent bot")
    description: str | None = Field(
        default=None, description="The description of the agent bot"
    )
    outgoing_url: str | None = Field(
        default=None, description="The webhook URL for the bot"
    )
    account_id: int | None = Field(
        default=None,
        description="The account ID to associate the agent bot with (Platform); tenant route overwrites.",
    )
    avatar_url: str | None = Field(
        default=None,
        description="URL tới jpeg/png; Chatwoot tải avatar bất đồng bộ (AvatarFromUrlJob).",
    )


class ChatwootAgentBotUpdateBody(BaseModel):
    """
    PATCH /platform/api/v1/agent_bots/{id} — cùng bộ field writable như create (swagger:
    `platform_agent_bot_create_update_payload`). Phải có ít nhất một trường được gửi.

    **account_id** không được áp dụng qua route tenant (server bỏ khỏi payload PATCH để tránh đổi account).
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = Field(default=None, min_length=1, description="The name of the agent bot")
    description: str | None = Field(default=None, description="The description of the agent bot")
    outgoing_url: str | None = Field(
        default=None, description="The webhook URL for the bot"
    )
    account_id: int | None = Field(
        default=None,
        description="Chỉ dùng nếu sau này có route platform thuần; route tenant bỏ qua.",
    )
    avatar_url: str | None = Field(
        default=None,
        description="URL tới jpeg/png cho avatar.",
    )


class ChatwootAgentBotRecord(BaseModel):
    """
    Shape phản hồi `agent_bot` từ Platform API (GET list/show, POST/PATCH 200) — theo swagger
    `components.schemas.agent_bot`. Dùng làm tài liệu / typing; response thực tế vẫn do Chatwoot trả về.
    """

    model_config = ConfigDict(extra="allow")

    id: int | None = Field(default=None, description="ID of the agent bot")
    name: str | None = Field(default=None, description="The name of the agent bot")
    description: str | None = Field(
        default=None, description="The description about the agent bot"
    )
    thumbnail: str | None = Field(default=None, description="The thumbnail of the agent bot")
    outgoing_url: str | None = Field(default=None, description="The webhook URL for the bot")
    bot_type: str | None = Field(default=None, description="The type of the bot")
    bot_config: dict[str, Any] | None = Field(
        default=None, description="The configuration of the bot"
    )
    account_id: int | None = Field(
        default=None, description="Account ID if it's an account specific bot"
    )
    access_token: str | None = Field(
        default=None, description="The access token for the bot"
    )
    system_bot: bool | None = Field(
        default=None, description="Whether the bot is a system bot"
    )


class ChatwootUserCreateBody(BaseModel):
    """Body POST /chatwoot/users/{user_id} — tạo user Platform Chatwoot và map với user nội bộ (user_id trên path)."""

    model_config = ConfigDict(extra="allow")

    name: str | None = Field(default=None, description="Full name")
    display_name: str | None = Field(default=None, description="Display name")
    email: str | None = Field(default=None, description="Email")
    password: str | None = Field(default=None, description="Password")
    custom_attributes: dict[str, Any] | None = Field(
        default=None, description="Custom attributes"
    )


class ChatwootUserUpdateBody(BaseModel):
    """PATCH /platform/api/v1/users/{id} — mọi trường tùy chọn."""

    model_config = ConfigDict(extra="allow")

    name: str | None = Field(default=None, description="Full name")
    display_name: str | None = Field(default=None, description="Display name")
    email: str | None = Field(default=None, description="Email")
    password: str | None = Field(default=None, description="Password")
    custom_attributes: dict[str, Any] | None = Field(
        default=None, description="Custom attributes"
    )


class ChatwootConversationAssignBody(BaseModel):
    """
    POST /api/v1/accounts/{account_id}/conversations/{conversation_id}/assignments
    ([Assign Conversation](https://developers.chatwoot.com/api-reference/conversation-assignments/assign-conversation)).

    - Gửi **assignee_agent_uuid** để gán agent (UUID nội bộ đã map với Chatwoot agent id).
    - Hoặc gửi **team_id** (id số trên Chatwoot). Nếu có cả hai, Chatwoot ưu tiên assignee.
    """

    model_config = ConfigDict(extra="forbid")

    assignee_agent_uuid: UUID | None = Field(
        default=None,
        description="UUID agent trong contact-center (bảng map); không gửi id số Chatwoot.",
    )
    team_id: int | None = Field(
        default=None,
        description="Id team trên Chatwoot (Application API dùng số).",
    )

    @model_validator(mode="after")
    def _require_assignee_or_team(self):
        if self.assignee_agent_uuid is None and self.team_id is None:
            raise ValueError("Cần assignee_agent_uuid hoặc team_id")
        return self


class ChatwootApplicationJsonBody(BaseModel):
    """Forward nguyên JSON body sang Chatwoot Application API (extra fields giữ nguyên key)."""

    model_config = ConfigDict(extra="allow")


class ChatwootConversationToggleStatusBody(BaseModel):
    """POST .../conversations/{id}/toggle_status — theo application_swagger.json."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["open", "resolved", "pending", "snoozed"]
    snoozed_until: int | float | None = Field(
        default=None,
        description="Unix timestamp (giây) khi snooze; chỉ dùng khi status=snoozed.",
    )


class ChatwootConversationLabelsMutationBody(BaseModel):
    """POST .../conversations/{id}/labels — ghi đè danh sách label."""

    model_config = ConfigDict(extra="forbid")

    labels: list[str]


class ChatwootConversationTypingBody(BaseModel):
    """POST .../conversations/{id}/toggle_typing_status."""

    model_config = ConfigDict(extra="forbid")

    typing_status: Literal["on", "off"]
    is_private: bool | None = None


class ChatwootConversationCustomAttributesBody(BaseModel):
    """POST .../conversations/{id}/custom_attributes."""

    model_config = ConfigDict(extra="forbid")

    custom_attributes: dict[str, Any]

class ChatwootBulkActionLabelsBody(BaseModel):
    """POST .../conversations/bulk_action_labels — thêm hoặc xóa label cho nhiều conversation."""
    type: Literal["Conversation"]
    ids: list[int]
    labels: dict[str, list[str]] = Field(default_factory=dict)
    fields: dict[str, Any] = Field(default_factory=dict)

class ConversationFilter(BaseModel):
    """Shape filter conversation khi gọi GET /api/v1/accounts/{account_id}/conversations."""
    attribute_key: str
    attribute_model: str | None = None
    filter_operator: str
    values: List[Union[str, int]]
    query_operator: str | None = None
    custom_attribute_type: str | None = None

class ConversationFilterRequest(BaseModel):
    payload: List[ConversationFilter]

class FilterPayload(BaseModel):
    attribute_key: str
    attribute_model: str
    filter_operator: str
    values: List[str]
    custom_attribute_type: str = ""


class FilterQuery(BaseModel):
    payload: List[FilterPayload]


class ChatwootCustomFiltersBody(BaseModel):
    name: str
    filter_type: Union[int, str]
    query: FilterQuery


class ChatwootTeamCreateBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, description="The name of the team")
    description: str | None = Field(default=None, description="The description of the team")
    allow_auto_assign: bool | None = Field(
        default=None, description="Whether to allow auto assignment of conversations"
    )


class ChatwootTeamUpdateBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = Field(default=None, min_length=1, description="The name of the team")
    description: str | None = Field(default=None, description="The description of the team")
    allow_auto_assign: bool | None = Field(
        default=None, description="Whether to allow auto assignment of conversations"
    )


class ChatwootTeamMembersBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_ids: list[UUID] = Field(
        description="List of local agent/user UUIDs to add/remove/update in the team"
    )