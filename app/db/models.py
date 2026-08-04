import uuid
import enum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy import Column, Integer, String, Text, ForeignKey, TIMESTAMP, Float, BigInteger, Boolean, Table, Enum, LargeBinary, UniqueConstraint, Index, text
from sqlalchemy.orm import relationship
from app.core.config.database import Base
from datetime import datetime, timedelta, timezone
from app.core.config.app_config import settings
from uuid6 import uuid7

def generate_uuid7():
    """Generate UUID V7"""
    return uuid7()

class TagType(str, enum.Enum):
    """Phân loại tag thuộc về đối tượng nào"""
    TICKET = "ticket"
    CUSTOMER = "customer"


class NotificationType(str, enum.Enum):
    """Notification types"""
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    SYSTEM = "system"
    TICKET_UPDATE = "ticket_update"

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    username = Column(String(255), unique=True, nullable=False)
    password = Column(String(255))
    email = Column(String(255))
    fullname = Column(String(100))
    create_day = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    chat_id = Column(BigInteger)
    is_active = Column(Integer, default=1)
    token_version = Column(Integer, default=0, nullable=False)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id"))
    level_id = Column(UUID(as_uuid=True), ForeignKey("levels.id"))
    tenant_id = Column(UUID(as_uuid=True), nullable=True)

    # WEBPHONE / SIP / 3CX
    webphone_enabled = Column(Boolean, default=False)
    sip_extension = Column(String(20))
    sip_username = Column(String(100))
    sip_password = Column(String(255))     
    sip_domain = Column(String(255))
    sip_ws_server = Column(String(255))
    sip_port = Column(Integer)
    sip_protocol = Column(String(10))
    webphone_api_key = Column(String(255))
    webphone_process_id = Column(String(50))
    webphone_agent_id = Column(String(50))
    call_recording_enabled = Column(Boolean, default=True)
    call_log_enabled = Column(Boolean, default=True)
    meta_data = Column(JSONB, nullable=True)

    # Relationships
    logs = relationship("Log", back_populates="user")
    refresh_tokens = relationship("RefreshToken", back_populates="user")
    role = relationship("Role", back_populates="users", lazy="selectin")
    level = relationship("Levels", back_populates="users", lazy="selectin")
    group_users = relationship("GroupUser", back_populates="user")
    

class Log(Base):
    __tablename__ = "logs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    user_name = Column(String(255))
    action= Column(String(255))
    create_time= Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    data= Column(Text)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    user = relationship("User", back_populates="logs")
    tenant_id = Column(UUID(as_uuid=True), nullable=True)

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    refresh_token = Column(String(512), nullable=False)
    ip = Column(String(255))
    user_agent = Column(String(512))
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    expired_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    user = relationship("User", back_populates="refresh_tokens")
    tenant_id = Column(UUID(as_uuid=True), nullable=True)

class Levels(Base):
    __tablename__ = "levels"
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    name = Column(String(50), nullable=False)
    description = Column(String(255))
    level_order = Column(Integer, nullable=False, default=0) # Higher number means higher level
    users = relationship("User", back_populates="level", lazy="selectin")
    

class Role(Base):
    __tablename__ = "roles"
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    name = Column(String(50),  nullable=False)
    description = Column(String(255))
    role_order = Column(Integer)
    users = relationship("User", back_populates="role", lazy="selectin")
    role_permissions = relationship("RolePermission", back_populates="role")
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    is_active = Column(Integer, default=1)
    
    
class RolePermission(Base):
    __tablename__ = "role_permissions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id"))
    permission_id = Column(UUID(as_uuid=True), ForeignKey("permissions.id"))
    role = relationship("Role", back_populates="role_permissions")
    permission = relationship("Permission", back_populates="role_permissions")
    tenant_id = Column(UUID(as_uuid=True), nullable=True)

class Permission(Base):
    __tablename__ = "permissions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    name = Column(String(50), nullable=False)
    description = Column(String(255))
    role_permissions = relationship("RolePermission", back_populates="permission")
    belong_to = Column(String(100), nullable=False, default="")
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    is_active = Column(Integer, default=1)
    
class GroupUser(Base):
    __tablename__ = "group_users"
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"))
    user = relationship("User", back_populates="group_users")
    group = relationship("Group", back_populates="group_users")
    tenant_id = Column(UUID(as_uuid=True), nullable=True)


class Group(Base):
    __tablename__ = "groups"
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    name = Column(String(50), nullable=False)
    description = Column(String(255))
    group_users = relationship("GroupUser", back_populates="group")
    department_id = Column(UUID(as_uuid=True), ForeignKey("departments.id"))
    department= relationship("Department", back_populates="groups")
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    is_active = Column(Integer, default=1)

    
class Department(Base):
    __tablename__ = "departments"
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    name = Column(String(50),  nullable=False)
    description = Column(String(255))
    groups = relationship("Group", back_populates="department")
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    is_active = Column(Integer, default=1)
   
class Tenant(Base):
    __tablename__ = "tenant"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    name = Column(String(50), nullable=False)
    description = Column(String(255))
    is_active = Column(Integer, default=1)
    partner_id = Column(UUID(as_uuid=True), nullable=True)
    # manhnx - merge graph: 18-06-2026
    graph_id = Column(UUID(as_uuid=True), nullable=True) # trường dùng để map với graph kg
    agent_id = Column(UUID(as_uuid=True), nullable=True) # trường dùng để map với agent kg để trả lời
    graph_activated = Column(Integer, default=0) # 0: chưa kích hoạt, 1: đã kích hoạt
    # manhnx - merge graph: 18-06-2026
    meta_data = Column(JSONB, nullable=True, default=lambda: {"chatbot_enabled": True, "default_responder": "bot"})
    # manhnx 30-07-2026: thêm trường để cấu hình webcall
    webcall_config = Column(JSONB, nullable=True, default=lambda: {
        "enable_widget": True, 
        "sip_only": True, 
        "sip_domain": "",   
        "ws_server": "", 
        "sip_password": "", 
        "api_key": ""
    })
    

# manhnx - 18-06-2026: lưu lại thông tin được cung cấp từ KH
class CustomerProvidedInfo(Base):
    __tablename__ = "customer_provided_info"
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True, index=True)
    phone = Column(String(20), nullable=True, index=True)
    description = Column(Text, nullable=True) # desc...
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

# Ticket System
class TicketStatus(str, enum.Enum):
    """Trạng thái của Ticket"""
    PENDING = "pending"           # Chờ xử lý
    OPEN = "open"                 # Đang mở
    IN_PROGRESS = "in_progress"   # Đang xử lý
    ON_HOLD = "on_hold"           # Tạm dừng
    RESOLVED = "resolved"         # Đã giải quyết
    CLOSED = "closed"             # Đã đóng
    CANCELLED = "cancelled"       # Đã hủy


class TicketPriority(str, enum.Enum):
    """Mức độ ưu tiên của Ticket"""
    LOW = "low"           # Thấp
    MEDIUM = "medium"     # Trung bình
    HIGH = "high"         # Cao
    URGENT = "urgent"     # Khẩn cấp
    CRITICAL = "critical" # Nghiêm trọng


class FlowInstanceStatus(str, enum.Enum):
    """Trạng thái của Flow Instance"""
    PENDING = "pending"       # Chờ bắt đầu
    RUNNING = "running"       # Đang chạy
    PAUSED = "paused"         # Tạm dừng
    COMPLETED = "completed"   # Hoàn thành
    FAILED = "failed"         # Thất bại
    CANCELLED = "cancelled"   # Đã hủy


# Association table for many-to-many relationship between tickets and tags
ticket_tag_association = Table(
    "ticket_tags",
    Base.metadata,
    Column("ticket_id", UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)

# Association table for many-to-many relationship between customers and tags
customer_tag_association = Table(
    "customer_tags",
    Base.metadata,
    Column("customer_id", UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class TicketTemplate(Base):
    __tablename__ = "ticket_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    flow_id = Column(UUID(as_uuid=True), nullable=True)
    sla_id = Column(UUID(as_uuid=True), nullable=True)
    extension_schema = Column(LargeBinary, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    # Relationships
    tickets = relationship("Ticket", back_populates="template", cascade="all, delete-orphan")


class TicketFlow(Base):
    __tablename__ = "ticket_flows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    steps = relationship("TicketFlowStep", back_populates="flow", cascade="all, delete-orphan", order_by="TicketFlowStep.step_order")
    tickets = relationship("Ticket", back_populates="flow")
    flow_instances = relationship("TicketFlowInstance", back_populates="flow", cascade="all, delete-orphan")


class TicketFlowStep(Base):
    __tablename__ = "ticket_flow_steps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    flow_id = Column(UUID(as_uuid=True), ForeignKey("ticket_flows.id", ondelete="CASCADE"), nullable=False, index=True)
    step_name = Column(String(255), nullable=False)
    step_order = Column(Integer, nullable=False)
    assignee = Column(String(100), nullable=True)  # Deprecated - use assignee_user_id or assignee_group_id
    assignee_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    assignee_group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    flow = relationship("TicketFlow", back_populates="steps")
    assignee_user = relationship("User", foreign_keys=[assignee_user_id], backref="assigned_flow_steps")
    assignee_group = relationship("Group", foreign_keys=[assignee_group_id], backref="assigned_flow_steps")


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    code = Column(String(50), nullable=False, unique=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Enum(TicketStatus), nullable=False, default=TicketStatus.PENDING, index=True)
    priority = Column(Enum(TicketPriority), nullable=True, default=TicketPriority.MEDIUM, index=True)
    template_id = Column(UUID(as_uuid=True), ForeignKey("ticket_templates.id", ondelete="SET NULL"), nullable=True, index=True)
    flow_id = Column(UUID(as_uuid=True), ForeignKey("ticket_flows.id", ondelete="SET NULL"), nullable=True, index=True)
    sla_id = Column(UUID(as_uuid=True), nullable=True)
    created_by = Column(UUID(as_uuid=True), nullable=False, index=True)
    assigned_to = Column(UUID(as_uuid=True), nullable=True, index=True)
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    closed_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Relationships
    template = relationship("TicketTemplate", back_populates="tickets")
    flow = relationship("TicketFlow", back_populates="tickets")
    extensions = relationship("TicketExtension", back_populates="ticket", cascade="all, delete-orphan", uselist=False)
    events = relationship("TicketEvent", back_populates="ticket", cascade="all, delete-orphan")
    contexts = relationship("TicketContext", back_populates="ticket", cascade="all, delete-orphan")
    flow_instances = relationship("TicketFlowInstance", back_populates="ticket", cascade="all, delete-orphan")
    tags = relationship("Tag", secondary=ticket_tag_association, back_populates="tickets")


class TicketExtension(Base):
    __tablename__ = "ticket_extensions"

    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True, nullable=False)
    data = Column(LargeBinary, nullable=True)

    # Relationships
    ticket = relationship("Ticket", back_populates="extensions")


class TicketContext(Base):
    __tablename__ = "ticket_contexts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    context_type = Column(String(50), nullable=False, index=True)
    context_id = Column(String(100), nullable=False)
    source_type = Column(String(50), nullable=True)
    context_metadata = Column(LargeBinary, nullable=True)  # Đổi tên từ metadata để tránh conflict với SQLAlchemy
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    tenant_id = Column(UUID(as_uuid=True), nullable=True)

    # Relationships
    ticket = relationship("Ticket", back_populates="contexts")


class TicketFlowInstance(Base):
    __tablename__ = "ticket_flow_instances"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    flow_id = Column(UUID(as_uuid=True), ForeignKey("ticket_flows.id", ondelete="CASCADE"), nullable=False, index=True)
    current_step_id = Column(UUID(as_uuid=True), nullable=True)
    status = Column(Enum(FlowInstanceStatus), nullable=False, default=FlowInstanceStatus.PENDING, index=True)
    started_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at = Column(TIMESTAMP(timezone=True), nullable=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True)

    # Relationships
    ticket = relationship("Ticket", back_populates="flow_instances")
    flow = relationship("TicketFlow", back_populates="flow_instances")


class TicketEvent(Base):
    __tablename__ = "ticket_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True) # là loại sự kiện: CREATED, UPDATED, REOPENED, CLOSED.
    payload = Column(LargeBinary, nullable=True)
    actor_type = Column(String(100), nullable=True) # (gán name của role của user khi thêm sự kiện)
    actor_id = Column(String(100), nullable=True, index=True) # để lại là chuỗi, để có thể gán linh hoạt (user_id, system, api, etc.)
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    # Relationships
    ticket = relationship("Ticket", back_populates="events")


class Tag(Base):
    __tablename__ = "tags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    color = Column(String(7), nullable=True)  # Hex color code
    type = Column(Enum(TagType), nullable=False, default=TagType.TICKET, index=True)
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    is_active = Column(Integer, default=1)

    # Relationships
    tickets = relationship("Ticket", secondary=ticket_tag_association, back_populates="tags")
    customers = relationship("Customer", secondary=customer_tag_association, back_populates="tags")


class Customer(Base):
    __tablename__ = "customers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    name = Column(String(255), nullable=False)
    phone = Column(String(20), nullable=True, index=True)
    email = Column(String(255), nullable=True, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    created_by = Column(UUID(as_uuid=True), nullable=True, index=True)
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    meta_data = Column(JSONB, nullable=True)
    is_active = Column(Integer, default=1)

    # Relationships
    tags = relationship("Tag", secondary=customer_tag_association, back_populates="customers")


class Notification(Base):
    """
    Persistent Notification Model
    Lưu trữ tất cả notifications để:
    - Gửi lại khi user reconnect
    - Track read/unread status
    - History notifications
    """
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    
    # Notification content
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    type = Column(Enum(NotificationType), default=NotificationType.INFO, nullable=False)
    
    # Recipient info
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)  # Null = broadcast
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)  # For tenant-wide notifications
    
    # Status tracking
    is_read = Column(Integer, default=0, nullable=False, index=True)  # 0=unread, 1=read
    read_at = Column(TIMESTAMP(timezone=True), nullable=True)
    
    # Delivery tracking
    delivered = Column(Integer, default=0, nullable=False)  # 0=pending, 1=delivered via websocket
    delivered_at = Column(TIMESTAMP(timezone=True), nullable=True)
    
    # Metadata
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=True)  # Optional expiration
    
    # Additional data (JSON string)
    data = Column(Text, nullable=True)  # Extra context as JSON
    
    # Sender info (optional)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id], backref="notifications")
    sender = relationship("User", foreign_keys=[sender_id], backref="sent_notifications")


class ChatwootMapResourceType(str, enum.Enum):
    """
    Mỗi dòng: `local_uuid` (contact-center) ↔ `chatwoot_id` (Chatwoot).
    Chỉ cần `resource_type` để biết đang map thực thể loại nào.
    """

    ACCOUNT = "account"
    USER = "user"
    AGENT = "agent"
    AGENT_BOT = "agent_bot"
    TEAM = "team"


class ChatwootLegacyMap(Base):
    """
    Map UUID nội bộ và id số trên Chatwoot.
    `resource_type`: account | user | agent | agent_bot.
    Cột `tenant_id` (khi có) phục vụ unique theo tenant, không thay thế vai trò phân loại của `resource_type`.
    """

    __tablename__ = "chatwoot_legacy_map"

    id = Column(Integer, primary_key=True, autoincrement=True)
    resource_type = Column(
        Enum(
            ChatwootMapResourceType,
            native_enum=False,
            values_callable=lambda t: [m.value for m in t],
        ),
        nullable=False,
        index=True,
    )
    local_uuid = Column(UUID(as_uuid=True), nullable=False)
    chatwoot_id = Column(Integer, nullable=False)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    created_at = Column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("resource_type", "local_uuid", name="uq_chatwoot_legacy_local"),
        Index("ix_chatwoot_legacy_type_local", "resource_type", "local_uuid"),
        Index(
            "uq_cwl_scoped_remote",
            "resource_type",
            "tenant_id",
            "chatwoot_id",
            unique=True,
            postgresql_where=text("tenant_id IS NOT NULL"),
        ),
        Index(
            "uq_cwl_user_remote",
            "resource_type",
            "chatwoot_id",
            unique=True,
            postgresql_where=text("resource_type = 'user'"),
        ),
    )


class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid7, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Định danh cuộc gọi từ tổng đài
    sip_call_id = Column(String(255), unique=True, nullable=False, index=True) 

    # Khớp nối thông tin ngữ cảnh (Context)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True) # Agent thực hiện cuộc gọi

    # Thông tin cuộc gọi
    direction = Column(String(20), default="outbound", nullable=False) # inbound hoặc outbound
    phone_number = Column(String(20), nullable=False) # Số điện thoại của khách hàng
    status = Column(String(50), nullable=True) # ringing, answered, ended, busy, missed...
    
    # Thời gian & File ghi âm
    started_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))
    ended_at = Column(TIMESTAMP(timezone=True), nullable=True)
    duration = Column(Integer, default=0) # Thời lượng cuộc gọi (giây)
    recording_url = Column(String(512), nullable=True) # Đường dẫn file ghi âm cuộc gọi
    
    # Metadata mở rộng
    meta_data = Column(JSONB, nullable=True)
    
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    tenant = relationship("Tenant", backref="call_logs")
    customer = relationship("Customer", backref="call_logs")
    ticket = relationship("Ticket", backref="call_logs")
    user = relationship("User", backref="call_logs")