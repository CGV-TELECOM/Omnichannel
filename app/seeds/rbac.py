from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Role, Permission, RolePermission, User, Levels, Tenant, ChatwootLegacyMap, ChatwootMapResourceType
from app.core.security.password_utils import hash_password
from sqlalchemy.future import select
from sqlalchemy import delete, func, or_
from uuid import UUID
from app.integrations.chatwoot import client as chatwoot_client

# Permission cũ dạng view detail by id — đã gộp vào view_* (list + detail).
OBSOLETE_PERMISSION_NAMES = frozenset({
    "view_level_by_id",
    "view_department_by_id",
    "view_group_by_id",
    "view_group_detail_by_id",
    "view_tag_by_id",
    "view_ticket_flow_by_id",
    "view_ticket_flow_instance_by_id",
    "view_ticket_flow_step_by_id",
    "view_customer_by_id",
    "view_roles_by_id",
    "view_permissions_by_id",
    # Gộp vào group / roles
    "view_user_groups",
    "delete_user_group",
    "view_role_permissions_by_role_id",
})


# Prefix action trong tên permission (dài → ngắn để match đúng).
_PERMISSION_ACTION_PREFIXES = (
    "assign",
    "manage",
    "create",
    "delete",
    "edit",
    "view",
    "send",
    "sync",
    "bulk",
    "current",
)

# belong_to đặc biệt — tránh gom nhiều quyền khác resource vào 1 row (FE matrix).
_PERMISSION_BELONG_TO_SPECIAL = {
    "current_user": "user",
    "assign_user_to_group": "group",
    "assign_permissions_to_role": "permission_role",
    "delete_permission_from_role": "permission_role",
    "assign_ticket": "ticket",
    "bulk_messaging_actions": "messaging_action",
    "sync_messaging_integration": "messaging_integration",
    "manage_messaging_inbox_members": "messaging_inbox",
    "manage_messaging_team_members": "messaging_team",
    "assign_messaging_conversation": "messaging_conversation",
    "send_messaging_message": "messaging_message",
    "delete_messaging_message": "messaging_message",
    "view_messaging_reports": "messaging",
    "view_logs": "log",
    # Call log — tách riêng để không đụng ô System/View
    "view_call_logs": "call_log",
    "create_call_log": "call_log",
    "edit_call_log": "call_log",
    "view_call_log_events": "call_log_event",
    "view_own_tenant_settings": "tenant_setting",
    "edit_own_tenant_settings": "tenant_setting",
}


def infer_permission_belong_to(name: str) -> str:
    """
    Suy ra belong_to (resource row trên UI matrix) từ tên permission.
    Mỗi (belong_to, action) chỉ nên có 1 permission — tránh 2 checkbox cùng ô.
    """
    if name in _PERMISSION_BELONG_TO_SPECIAL:
        return _PERMISSION_BELONG_TO_SPECIAL[name]

    for prefix in _PERMISSION_ACTION_PREFIXES:
        token = f"{prefix}_"
        if name.startswith(token):
            resource = name[len(token) :]
            # Chuẩn hoá số nhiều: users→user, inboxes→inbox, events→event
            if resource.endswith("ies"):
                resource = resource[:-3] + "y"
            elif resource.endswith(("xes", "zes", "ches", "shes", "sses")):
                resource = resource[:-2]
            elif resource.endswith("ses"):
                resource = resource[:-2]
            elif resource.endswith("s") and not resource.endswith("ss"):
                resource = resource[:-1]
            return resource or "system"

    return "system"


# Permissions chỉ dành cho Admin Platform (CGV) — mutate catalog / tenant hệ thống.
# Tenant admin (admin-partner) KHÔNG được có các quyền này.
# Role CRUD + gán permission: partner được phép trong tenant (không nằm list này).
PLATFORM_ONLY_PERMISSION_NAMES = frozenset({
    "create_permissions",
    "edit_permissions",
    "delete_permissions",
    "create_tenant",
    "edit_tenant",
    "delete_tenant",
    "create_level",
    "edit_level",
    "delete_level",
    # Messaging platform account — provision toàn hệ thống
    "create_messaging_account",
    "edit_messaging_account",
    "delete_messaging_account",
    "sync_messaging_integration",
})

# Permission mặc định cho role "user" (agent vận hành) trong mỗi tenant
DEFAULT_USER_PERMISSION_NAMES = (
    "current_user",
    "view_users",
    "view_levels",
    "view_departments",
    "view_groups",
    "view_tags",
    "create_tag",
    "edit_tag",
    "view_tickets",
    "create_ticket",
    "edit_ticket",
    "assign_ticket",
    "view_ticket_events",
    "create_ticket_event",
    "view_ticket_templates",
    "view_ticket_contexts",
    "create_ticket_context",
    "view_ticket_extensions",
    "create_ticket_extension",
    "view_ticket_flows",
    "view_ticket_flow_instances",
    "create_ticket_flow_instance",
    "edit_ticket_flow_instance",
    "view_ticket_flow_steps",
    "view_customers",
    "create_customer",
    "edit_customer",
    "view_customer_provided_info",
    "create_customer_provided_info",
    "edit_customer_provided_info",
    "view_messaging_accounts",
    "view_messaging_conversations",
    "create_messaging_conversation",
    "edit_messaging_conversation",
    "send_messaging_message",
    "assign_messaging_conversation",
    "view_messaging_inboxes",
    "view_messaging_labels",
    "create_messaging_label",
    "bulk_messaging_actions",
    "view_messaging_custom_filters",
    "create_messaging_custom_filter",
    "edit_messaging_custom_filter",
    "view_messaging_agents",
    "view_messaging_teams",
    "view_messaging_agent_bots",
    "view_messaging_users",
    "view_messaging_reports",
    "view_call_logs",
    "create_call_log",
    "edit_call_log",
    "view_call_log_events",
)


async def seed_rbac(db: AsyncSession):
    """
    Seeds the database with initial roles, permissions, levels and an admin user.
    All IDs are now UUID V7 instead of Integer.

    Roles:
    - admin (Admin Platform / CGV): tenant_id NULL, full permissions, is_platform_admin user
    - admin-partner: per-tenant admin khách hàng — không mutate catalog permission/tenant/level
    - user: per-tenant agent vận hành least privilege
    """
    # Step 0: Create a default tenant if it doesn't exist (optional, for multi-tenant support)
    default_tenant = await _get_or_create_tenant(db, "Default Tenant", "Default tenant for seed data")

    # Step 0.5: Xóa permission by-id đã gộp + gỡ gán role
    await _purge_obsolete_permissions(db)
    # Step 1: Create permissions if they don't exist
    permission_names = [
        "view_users",
        "current_user",
        "delete_users",
        "create_users",
        "edit_users",
        "edit_roles",
        "view_roles",
        "delete_roles",
        "create_roles",
        "view_permissions",
        "edit_permissions",
        "delete_permissions",
        "create_permissions",
        "assign_permissions_to_role",
        "delete_permission_from_role",
        "view_logs",
        "view_levels",
        "create_level",
        "edit_level",
        "delete_level",
        "view_departments",
        "create_department",
        "edit_department",
        "delete_department",
        "view_groups",
        "create_group",
        "edit_group",
        "delete_group",
        "assign_user_to_group",
        "view_tags",
        "create_tag",
        "edit_tag",
        "delete_tag",
        # Ticket CRUD permissions
        "view_tickets",
        "create_ticket",
        "edit_ticket",
        "delete_ticket",
        "assign_ticket",
        # Ticket Events permissions
        "view_ticket_events",
        "create_ticket_event",
        "edit_ticket_event",
        "delete_ticket_event",
        "view_ticket_templates",
        "create_ticket_template",
        "edit_ticket_template",
        "delete_ticket_template",
        "view_ticket_contexts",
        "create_ticket_context",
        "edit_ticket_context",
        "delete_ticket_context",
        "view_ticket_extensions",
        "create_ticket_extension",
        "edit_ticket_extension",
        "delete_ticket_extension",
        # Ticket Flow permissions
        "view_ticket_flows",
        "create_ticket_flow",
        "edit_ticket_flow",
        "delete_ticket_flow",
        # Ticket Flow Instance permissions
        "view_ticket_flow_instances",
        "create_ticket_flow_instance",
        "edit_ticket_flow_instance",
        "delete_ticket_flow_instance",
        # Ticket Flow Step permissions
        "view_ticket_flow_steps",
        "create_ticket_flow_step",
        "edit_ticket_flow_step",
        "delete_ticket_flow_step",
        # Customer permissions
        "view_customers",
        "create_customer",
        "edit_customer",
        "delete_customer",
        # Customer Provided Info permissions
        "view_customer_provided_info",
        "create_customer_provided_info",
        "edit_customer_provided_info",
        "delete_customer_provided_info",
        # Tenant permissions
        "view_tenants",
        "create_tenant",
        "edit_tenant",
        "delete_tenant",
        # Cài đặt vận hành tenant (admin-partner) — không nằm PLATFORM_ONLY
        "view_own_tenant_settings",
        "edit_own_tenant_settings",
        # Messaging (omnichannel) — public API /messaging/*
        "view_messaging_accounts",
        "create_messaging_account",
        "edit_messaging_account",
        "delete_messaging_account",
        "sync_messaging_integration",
        "view_messaging_conversations",
        "create_messaging_conversation",
        "edit_messaging_conversation",
        "delete_messaging_conversation",
        "send_messaging_message",
        "delete_messaging_message",
        "assign_messaging_conversation",
        "view_messaging_inboxes",
        "create_messaging_inbox",
        "edit_messaging_inbox",
        "manage_messaging_inbox_members",
        "view_messaging_labels",
        "create_messaging_label",
        "delete_messaging_label",
        "bulk_messaging_actions",
        "view_messaging_custom_filters",
        "create_messaging_custom_filter",
        "edit_messaging_custom_filter",
        "delete_messaging_custom_filter",
        "view_messaging_agents",
        "create_messaging_agent",
        "edit_messaging_agent",
        "delete_messaging_agent",
        "view_messaging_teams",
        "create_messaging_team",
        "edit_messaging_team",
        "delete_messaging_team",
        "manage_messaging_team_members",
        "view_messaging_agent_bots",
        "create_messaging_agent_bot",
        "edit_messaging_agent_bot",
        "delete_messaging_agent_bot",
        "view_messaging_users",
        "create_messaging_user",
        "edit_messaging_user",
        "delete_messaging_user",
        "view_messaging_reports",
        # Call log / telephony timeline
        "view_call_logs",
        "create_call_log",
        "edit_call_log",
        "view_call_log_events",
    ]
    permissions = []
    for name in permission_names:
        stmt = select(Permission).filter_by(name=name)
        result = await db.execute(stmt)
        # Dữ liệu cũ có thể đã bị trùng name, không dùng scalar_one_or_none để tránh crash startup.
        permission = result.scalars().first()
        expected_belong_to = infer_permission_belong_to(name)
        if not permission:
            permission = Permission(
                name=name,
                description=f"Permission to {name}",
                belong_to=expected_belong_to,
            )
            db.add(permission)
            await db.commit()
            await db.refresh(permission)
        elif permission.belong_to != expected_belong_to:
            permission.belong_to = expected_belong_to
            await db.commit()
            await db.refresh(permission)
        permissions.append(permission)

    # Đồng bộ belong_to cho mọi permission (kể cả ngoài list seed) — tránh ô matrix trùng checkbox
    await _sync_all_permission_belong_to(db)

    # Step 2: Roles
    # - admin: Admin Platform (CGV) — tenant_id NULL
    # - admin-partner + user: seed theo Default Tenant qua seed_tenant_default_roles
    admin_role = await _get_or_create_role(
        db,
        "admin",
        "Admin Platform (CGV) — quản trị toàn hệ thống, cross-tenant, quản lý catalog role/permission/tenant",
        1000,
        tenant_id=None,
    )

    # Step 3: Levels
    levels = [
        {
            "name": "Admin",
            "description": "Level Admin Platform (CGV ops) — chỉ dùng cho tài khoản is_platform_admin",
            "level_order": 1000,
        },
        {
            "name": "Manager",
            "description": "Level quản lý tenant / admin khách hàng",
            "level_order": 100,
        },
        {
            "name": "Staff",
            "description": "Level nhân viên / team lead trong tenant",
            "level_order": 10,
        },
        {
            "name": "User",
            "description": "Level agent vận hành thường",
            "level_order": 1,
        },
    ]
    
    created_levels = []
    for level_data in levels:
        stmt = select(Levels).filter_by(name=level_data["name"])
        result = await db.execute(stmt)
        level = result.scalar_one_or_none()
        if not level:
            level = Levels(
                name=level_data["name"],
                description=level_data["description"],
                level_order=level_data["level_order"]
            )
            db.add(level)
            await db.commit()
            await db.refresh(level)
        else:
            # Đồng bộ mô tả level theo kiến trúc mới
            if level.description != level_data["description"]:
                level.description = level_data["description"]
                await db.commit()
                await db.refresh(level)
        created_levels.append(level)

    # Step 4: Sync permissions — admin platform full
    await _sync_permissions_to_role(db, admin_role, permissions)

    # Step 4b: Seed/sync admin-partner + user cho mọi tenant (kể cả Default)
    partner_role = None
    user_role = None
    tenants_result = await db.execute(
        select(Tenant).where(or_(Tenant.is_active == 1, Tenant.is_active.is_(None)))
    )
    all_tenants = list(tenants_result.scalars().all())
    if not all_tenants and default_tenant:
        all_tenants = [default_tenant]

    for tenant in all_tenants:
        seeded = await seed_tenant_default_roles(db, tenant.id, permissions=permissions)
        if default_tenant and tenant.id == default_tenant.id:
            partner_role = seeded.get("admin-partner")
            user_role = seeded.get("user")

    # Deactivate leftover global admin-partner / user (pre-migration templates)
    await _deactivate_global_tenant_template_roles(db)

    partner_count = 0
    user_count = 0
    if partner_role:
        partner_count = (
            await db.execute(
                select(RolePermission).where(RolePermission.role_id == partner_role.id)
            )
        ).scalars().all()
        partner_count = len(partner_count)
    if user_role:
        user_count = (
            await db.execute(
                select(RolePermission).where(RolePermission.role_id == user_role.id)
            )
        ).scalars().all()
        user_count = len(user_count)

    print(
        f"📋 Role sync: admin={len(permissions)} | "
        f"admin-partner={partner_count} | user={user_count}"
    )

    # Step 5: Create an admin user if they don't exist
    super_admin_level = next((l for l in created_levels if l.name == "Admin"), None)
    admin_user = await _create_admin_user(
        db, admin_role, super_admin_level,
        tenant_id=default_tenant.id if default_tenant else None,
    )

    # Step 6: Create a regular user (role user của Default Tenant)
    user_level = next((l for l in created_levels if l.name == "User"), None)
    if user_role:
        await _create_regular_user(
            db, user_role, user_level,
            tenant_id=default_tenant.id if default_tenant else None,
        )

    # Step 7: Create default Chatwoot mappings for Tenant and Admin
    await _seed_chatwoot_mappings(db, default_tenant, admin_user)

    print("✅ Seed RBAC thành công!")


async def seed_tenant_default_roles(
    db: AsyncSession,
    tenant_id: UUID,
    permissions: list[Permission] | None = None,
) -> dict[str, Role]:
    """
    Tạo (hoặc đồng bộ) role mặc định admin-partner + user cho một tenant.
    Dùng khi seed Default Tenant và khi createTenant.
    """
    if permissions is None:
        result = await db.execute(select(Permission).where(Permission.is_active == 1))
        permissions = list(result.scalars().all())

    perm_by_name = {p.name: p for p in permissions}

    partner_role = await _get_or_create_role(
        db,
        "admin-partner",
        "Admin khách hàng (tenant) — quản lý user/org/ticket/messaging trong tenant; không sửa catalog hệ thống",
        100,
        tenant_id=tenant_id,
    )
    user_role = await _get_or_create_role(
        db,
        "user",
        "Agent vận hành — xử lý ticket/conversation/customer trong phạm vi được giao",
        10,
        tenant_id=tenant_id,
    )

    partner_perms = [
        p for p in permissions if p.name not in PLATFORM_ONLY_PERMISSION_NAMES
    ]
    await _sync_permissions_to_role(db, partner_role, partner_perms)

    user_perms = [
        perm_by_name[n] for n in DEFAULT_USER_PERMISSION_NAMES if n in perm_by_name
    ]
    await _sync_permissions_to_role(db, user_role, user_perms)

    print(f"✅ Seed default roles cho tenant {tenant_id}: admin-partner, user")
    return {"admin-partner": partner_role, "user": user_role}


async def _deactivate_global_tenant_template_roles(db: AsyncSession) -> None:
    """Soft-deactivate admin-partner/user còn tenant_id NULL (template cũ)."""
    result = await db.execute(
        select(Role).where(
            Role.tenant_id.is_(None),
            func.lower(Role.name).in_(["admin-partner", "user"]),
            Role.is_active == 1,
        )
    )
    roles = list(result.scalars().all())
    if not roles:
        return
    for role in roles:
        role.is_active = 0
    await db.commit()
    print(
        f"🗑️  Deactivate global template roles: "
        f"{', '.join(r.name for r in roles)}"
    )


async def _sync_all_permission_belong_to(db: AsyncSession) -> None:
    """Cập nhật belong_to theo infer_permission_belong_to cho toàn catalog."""
    result = await db.execute(select(Permission))
    perms = list(result.scalars().all())
    updated = 0
    for perm in perms:
        expected = infer_permission_belong_to(perm.name)
        if perm.belong_to != expected:
            perm.belong_to = expected
            updated += 1
    if updated:
        await db.commit()
        print(f"🔄 Sync belong_to: đã cập nhật {updated} permission(s)")
    else:
        print("ℹ️  belong_to đã khớp, không cần sync.")


async def _purge_obsolete_permissions(db: AsyncSession) -> None:
    """
    Xóa permission obsolete đã gộp (view_*_by_id, user_group → group, role-permissions → view_roles)
    và các RolePermission liên quan.
    """
    result = await db.execute(
        select(Permission).where(Permission.name.in_(OBSOLETE_PERMISSION_NAMES))
    )
    obsolete = list(result.scalars().all())
    if not obsolete:
        print("ℹ️  Không có permission obsolete cần xóa.")
        return

    perm_ids = [p.id for p in obsolete]
    names = [p.name for p in obsolete]

    del_rp = await db.execute(
        delete(RolePermission).where(RolePermission.permission_id.in_(perm_ids))
    )
    del_p = await db.execute(
        delete(Permission).where(Permission.id.in_(perm_ids))
    )
    await db.commit()
    print(
        f"🗑️  Đã xóa {del_p.rowcount} permission obsolete "
        f"và {del_rp.rowcount} role_permission: {', '.join(names)}"
    )


async def _get_or_create_role(
    db: AsyncSession,
    role_name: str,
    description: str,
    role_order: int,
    tenant_id: UUID | None = None,
) -> Role:
    """
    Gets an existing role or creates a new one (scoped by tenant_id).
    Nếu đã tồn tại: cập nhật description / role_order / is_active / tenant_id cho khớp seed.
    """
    if tenant_id is None:
        stmt = select(Role).where(
            func.lower(Role.name) == role_name.lower(),
            Role.tenant_id.is_(None),
        )
    else:
        stmt = select(Role).where(
            func.lower(Role.name) == role_name.lower(),
            Role.tenant_id == tenant_id,
        )
    result = await db.execute(stmt)
    role = result.scalar_one_or_none()
    if not role:
        role = Role(
            name=role_name,
            description=description,
            role_order=role_order,
            tenant_id=tenant_id,
        )
        db.add(role)
        await db.commit()
        await db.refresh(role)
    else:
        changed = False
        if role.description != description:
            role.description = description
            changed = True
        if role.role_order != role_order:
            role.role_order = role_order
            changed = True
        if role.is_active != 1:
            role.is_active = 1
            changed = True
        if role.tenant_id != tenant_id:
            role.tenant_id = tenant_id
            changed = True
        if changed:
            await db.commit()
            await db.refresh(role)
    return role

async def _assign_permissions_to_role(db: AsyncSession, role: Role, permissions: list[Permission]):
    """
    Assigns permissions to a role (chỉ thêm, không gỡ).

    Args:
        db: The database session.
        role: The Role object (with UUID id).
        permissions: A list of Permission objects (with UUID ids).
    """
    for perm in permissions:
        stmt = select(RolePermission).filter_by(role_id=role.id, permission_id=perm.id)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if not existing:
            db.add(RolePermission(
                role_id=role.id, 
                permission_id=perm.id,
            ))
    await db.commit()


async def _sync_permissions_to_role(db: AsyncSession, role: Role, permissions: list[Permission]):
    """
    Đồng bộ đúng tập permission cho role: thêm thiếu + xóa thừa.
    Dùng cho role "user" (least privilege) để seed có thể thu hẹp quyền trên DB đã chạy.
    """
    desired_ids = {p.id for p in permissions}

    existing = (
        await db.execute(
            select(RolePermission).where(RolePermission.role_id == role.id)
        )
    ).scalars().all()

    removed = 0
    for rp in existing:
        if rp.permission_id not in desired_ids:
            await db.delete(rp)
            removed += 1

    added = 0
    existing_ids = {rp.permission_id for rp in existing}
    for perm in permissions:
        if perm.id not in existing_ids:
            db.add(RolePermission(
                role_id=role.id,
                permission_id=perm.id,
            ))
            added += 1

    await db.commit()
    print(
        f"🔄 Sync role '{role.name}': +{added} / -{removed} "
        f"(còn {len(desired_ids)} permissions)"
    )

async def _create_admin_user(
    db: AsyncSession,
    role: Role,
    level: Levels | None = None,
    tenant_id: UUID | None = None,
):
    """
    Creates the admin user if it does not exist.
    
    Args:
        db: The database session.
        role: The Role object (with UUID id).
        level: Optional Levels object (with UUID id).
        tenant_id: Tenant gắn cho user seed (không lấy từ Role).
    """
    stmt = select(User).filter_by(username="admin")
    result = await db.execute(stmt)
    admin_user = result.scalar_one_or_none()
    if not admin_user:
        admin_user = User(
            username="admin",
            password=hash_password("admin123"),
            fullname="Admin Platform (CGV)",
            role_id=role.id,  # UUID
            level_id=level.id if level else None,  # UUID or None
            is_active=1,
            email="admin@example.com",
            tenant_id=tenant_id,
            is_platform_admin=True,
        )
        db.add(admin_user)
        await db.commit()
        await db.refresh(admin_user)
    else:
        changed = False
        if not admin_user.is_platform_admin:
            admin_user.is_platform_admin = True
            changed = True
        if admin_user.role_id != role.id:
            admin_user.role_id = role.id
            changed = True
        if level and admin_user.level_id != level.id:
            admin_user.level_id = level.id
            changed = True
        if admin_user.fullname != "Admin Platform (CGV)":
            admin_user.fullname = "Admin Platform (CGV)"
            changed = True
        if changed:
            await db.commit()
            await db.refresh(admin_user)
    return admin_user

async def _create_regular_user(
    db: AsyncSession,
    role: Role,
    level: Levels | None = None,
    tenant_id: UUID | None = None,
):
    """
    Creates a regular user if it does not exist.
    
    Args:
        db: The database session.
        role: The Role object (with UUID id).
        level: Optional Levels object (with UUID id).
        tenant_id: Tenant gắn cho user seed (không lấy từ Role).
    """
    stmt = select(User).filter_by(username="user")
    result = await db.execute(stmt)
    regular_user = result.scalar_one_or_none()
    if not regular_user:
        regular_user = User(
            username="user",
            password=hash_password("password123"),
            fullname="Regular User",
            role_id=role.id,  # UUID
            level_id=level.id if level else None,  # UUID or None
            is_active=1,
            email="user@example.com",
            tenant_id=tenant_id,
        )
        db.add(regular_user)
        await db.commit()
        await db.refresh(regular_user)
    return regular_user

async def _get_or_create_tenant(db: AsyncSession, name: str, description: str) -> Tenant | None:
    """
    Gets an existing tenant or creates a new one.
    
    Args:
        db: The database session.
        name: The name of the tenant.
        description: The description of the tenant.
        
    Returns:
        The Tenant object or None if creation fails.
    """
    try:
        stmt = select(Tenant).filter_by(name=name)
        result = await db.execute(stmt)
        tenant = result.scalar_one_or_none()
        if not tenant:
            tenant = Tenant(
                name=name,
                description=description,
                is_active=1,
                graph_activated=1,
                agent_id=UUID("b10add77-0a1b-4974-9411-15ff68de61cd"),
                graph_id=UUID("b10add77-0a1b-4974-9411-15ff68de61cd"),
                meta_data={"chatbot_enabled": True, "default_responder": "bot"}
            )
            db.add(tenant)
            await db.commit()
            await db.refresh(tenant)
        return tenant
    except Exception as e:
        print(f"Warning: Could not create tenant: {e}")
        return None

async def _seed_chatwoot_mappings(db: AsyncSession, default_tenant: Tenant | None, admin_user: User):
    """
    Seeds default chatwoot mappings for multi-tenant and SSO functionality.
    Queries Chatwoot API dynamically using the configured CHATWOOT_USER_API_TOKEN.
    """
    if not default_tenant:
        return
        
    chatwoot_user_id = None
    chatwoot_account_id = None
    
    # Try querying Chatwoot API dynamically first
    try:
        res = await chatwoot_client.application_request("GET", "/api/v1/profile")
        if res.status_code == 200 and isinstance(res.data, dict):
            chatwoot_user_id = res.data.get("id")
            accounts = res.data.get("accounts")
            if accounts and isinstance(accounts, list) and len(accounts) > 0:
                chatwoot_account_id = accounts[0].get("id")
    except Exception as e:
        print(f"Warning: Could not connect to Chatwoot to fetch profile: {e}")
        
    # Fallback to defaults (Account 1, User 1) if not set or dynamic fetch fails
    if chatwoot_user_id is None:
        chatwoot_user_id = 1
    if chatwoot_account_id is None:
        chatwoot_account_id = 1
        
    print(f"Mapping local entities to Chatwoot: Account ID {chatwoot_account_id}, User ID {chatwoot_user_id}")
    
    # 1. Map Tenant to Chatwoot Account
    stmt_acc = select(ChatwootLegacyMap).filter_by(
        resource_type=ChatwootMapResourceType.ACCOUNT,
        local_uuid=default_tenant.id
    )
    result_acc = await db.execute(stmt_acc)
    mapping_acc = result_acc.scalar_one_or_none()
    if not mapping_acc:
        mapping_acc = ChatwootLegacyMap(
            resource_type=ChatwootMapResourceType.ACCOUNT,
            local_uuid=default_tenant.id,
            chatwoot_id=chatwoot_account_id
        )
        db.add(mapping_acc)
        
    # 2. Map Admin User to Chatwoot User
    stmt_usr = select(ChatwootLegacyMap).filter_by(
        resource_type=ChatwootMapResourceType.USER,
        local_uuid=admin_user.id
    )
    result_usr = await db.execute(stmt_usr)
    mapping_usr = result_usr.scalar_one_or_none()
    if not mapping_usr:
        mapping_usr = ChatwootLegacyMap(
            resource_type=ChatwootMapResourceType.USER,
            local_uuid=admin_user.id,
            chatwoot_id=chatwoot_user_id
        )
        db.add(mapping_usr)
        
    # 3. Map Admin User to Chatwoot Agent under the default tenant
    stmt_agt = select(ChatwootLegacyMap).filter_by(
        resource_type=ChatwootMapResourceType.AGENT,
        local_uuid=admin_user.id,
        tenant_id=default_tenant.id
    )
    result_agt = await db.execute(stmt_agt)
    mapping_agt = result_agt.scalar_one_or_none()
    if not mapping_agt:
        mapping_agt = ChatwootLegacyMap(
            resource_type=ChatwootMapResourceType.AGENT,
            local_uuid=admin_user.id,
            chatwoot_id=chatwoot_user_id,
            tenant_id=default_tenant.id
        )
        db.add(mapping_agt)
        
    await db.commit()
