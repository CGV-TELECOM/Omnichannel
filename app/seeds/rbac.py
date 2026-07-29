from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Role, Permission, RolePermission, User, Levels, Tenant, ChatwootLegacyMap, ChatwootMapResourceType
from app.core.security.password_utils import hash_password
from sqlalchemy.future import select
from uuid import UUID
from app.integrations.chatwoot import client as chatwoot_client

async def seed_rbac(db: AsyncSession):
    """
    Seeds the database with initial roles, permissions, levels and an admin user.
    All IDs are now UUID V7 instead of Integer.
    """
    # Step 0: Create a default tenant if it doesn't exist (optional, for multi-tenant support)
    default_tenant = await _get_or_create_tenant(db, "Default Tenant", "Default tenant for seed data")
    
    # Step 1: Create permissions if they don't exist
    permission_names = [
        "view_users",
        "current_user",
        "delete_users",
        "create_users",
        "edit_users",
        "view_user_groups",
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
        "view_level_by_id",
        "create_level",
        "edit_level",
        "delete_level",
        "view_departments",
        "view_department_by_id",
        "create_department",
        "edit_department",
        "delete_department",
        "view_groups",
        "view_group_by_id",
        "view_group_detail_by_id",
        "create_group",
        "edit_group",
        "delete_group",
        "assign_user_to_group",
        "delete_user_group",
        "view_role_permissions_by_role_id",
        "view_tags",
        "view_tag_by_id",
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
        "view_ticket_flow_by_id",
        "create_ticket_flow",
        "edit_ticket_flow",
        "delete_ticket_flow",
        # Ticket Flow Instance permissions
        "view_ticket_flow_instances",
        "view_ticket_flow_instance_by_id",
        "create_ticket_flow_instance",
        "edit_ticket_flow_instance",
        "delete_ticket_flow_instance",
        # Ticket Flow Step permissions
        "view_ticket_flow_steps",
        "view_ticket_flow_step_by_id",
        "create_ticket_flow_step",
        "edit_ticket_flow_step",
        "delete_ticket_flow_step",
        # Customer permissions
        "view_customers",
        "view_customer_by_id",
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
    ]
    permissions = []
    for name in permission_names:
        stmt = select(Permission).filter_by(name=name)
        result = await db.execute(stmt)
        # Dữ liệu cũ có thể đã bị trùng name, không dùng scalar_one_or_none để tránh crash startup.
        permission = result.scalars().first()
        if not permission:
            permission = Permission(
                name=name, 
                description=f"Permission to {name}",
                tenant_id=default_tenant.id if default_tenant else None,
                belong_to="system"
            )
            db.add(permission)
            await db.commit()
            await db.refresh(permission)
        permissions.append(permission)

    # Step 2: Create roles "admin" and "user" if they don't exist
    admin_role_name = "admin"
    user_role_name = "user"

    admin_role = await _get_or_create_role(db, admin_role_name, "Administrator role", 1000, default_tenant.id if default_tenant else None)
    user_role = await _get_or_create_role(db, user_role_name, "Regular user role", 10, default_tenant.id if default_tenant else None)

    # Step 3: Create levels if they don't exist
    levels = [
        {"name": "Admin", "description": "Administrator level", "level_order": 1000},
        {"name": "Manager", "description": "Manager level", "level_order": 100},
        {"name": "Staff", "description": "Staff level", "level_order": 10},
        {"name": "User", "description": "Regular user level", "level_order": 1}
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
        created_levels.append(level)

    # Step 4: Assign permissions to roles
    await _assign_permissions_to_role(db, admin_role, permissions)

    # Assign some permissions to the "user" role.  Adjust as needed for your application.
    user_permissions = [
        "current_user",  # Example:  Users can view items.
        "create_users",
        "edit_users",
        "view_users",
        "view_user_groups",
        "delete_users",
        "view_levels",
        "view_level_by_id",
        "view_departments",
        "view_department_by_id",
        "create_department",
        "edit_department",
        "delete_department",
        "view_groups",
        "view_group_by_id",
        "view_group_detail_by_id",
        "create_group",
        "edit_group",
        "delete_group",
        "view_roles",
        "view_tags",
        "view_tag_by_id",
        "create_tag",
        "edit_tag",
        # Ticket permissions
        "view_tickets",
        "create_ticket",
        "edit_ticket",
        "assign_ticket",
        # Ticket related permissions
        "view_ticket_events",
        "create_ticket_event",
        "view_ticket_templates",
        "view_ticket_contexts",
        "create_ticket_context",
        "view_ticket_extensions",
        "create_ticket_extension",
        # Ticket Flow permissions
        "view_ticket_flows",
        "view_ticket_flow_by_id",
        "create_ticket_flow",
        "edit_ticket_flow",
        # Ticket Flow Instance permissions
        "view_ticket_flow_instances",
        "view_ticket_flow_instance_by_id",
        "create_ticket_flow_instance",
        "edit_ticket_flow_instance",
        # Ticket Flow Step permissions
        "view_ticket_flow_steps",
        "view_ticket_flow_step_by_id",
        "create_ticket_flow_step",
        "edit_ticket_flow_step",
        # Customer (user thường chỉ được xem/tạo/sửa, KHÔNG được xóa)
        "view_customers",
        "view_customer_by_id",
        "create_customer",
        "edit_customer",
        # Customer Provided Info (user thường chỉ được xem/tạo/sửa, KHÔNG được xóa)
        "view_customer_provided_info",
        "create_customer_provided_info",
        "edit_customer_provided_info",
        "view_tenants",
    ]
    user_permissions_objects = [p for p in permissions if p.name in user_permissions]
    await _assign_permissions_to_role(db, user_role, user_permissions_objects)

    # Step 5: Create an admin user if they don't exist
    super_admin_level = next((l for l in created_levels if l.name == "Admin"), None)
    admin_user = await _create_admin_user(db, admin_role, super_admin_level)

    # Step 6: Create a regular user.
    user_level = next((l for l in created_levels if l.name == "User"), None)
    await _create_regular_user(db, user_role, user_level)

    # Step 7: Create default Chatwoot mappings for Tenant and Admin
    await _seed_chatwoot_mappings(db, default_tenant, admin_user)

    print("✅ Seed RBAC thành công!")

async def _get_or_create_role(db: AsyncSession, role_name: str, description: str, role_order: int, tenant_id: UUID | None = None) -> Role:
    """
    Gets an existing role or creates a new one.

    Args:
        db: The database session.
        role_name: The name of the role.
        description: The description of the role.
        role_order: The order of the role.
        tenant_id: Optional tenant ID (UUID).

    Returns:
        The Role object.
    """
    stmt = select(Role).filter_by(name=role_name)
    result = await db.execute(stmt)
    role = result.scalar_one_or_none()
    if not role:
        role = Role(
            name=role_name, 
            description=description, 
            role_order=role_order,
            tenant_id=tenant_id
        )
        db.add(role)
        await db.commit()
        await db.refresh(role)
    return role

async def _assign_permissions_to_role(db: AsyncSession, role: Role, permissions: list[Permission]):
    """
    Assigns permissions to a role.

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
                tenant_id=role.tenant_id
            ))
    await db.commit()

async def _create_admin_user(db: AsyncSession, role: Role, level: Levels | None = None):
    """
    Creates the admin user if it does not exist.
    
    Args:
        db: The database session.
        role: The Role object (with UUID id).
        level: Optional Levels object (with UUID id).
    """
    stmt = select(User).filter_by(username="admin")
    result = await db.execute(stmt)
    admin_user = result.scalar_one_or_none()
    if not admin_user:
        admin_user = User(
            username="admin",
            password=hash_password("admin123"),
            fullname="Admin User",
            role_id=role.id,  # UUID
            level_id=level.id if level else None,  # UUID or None
            is_active=1,
            email="admin@example.com",
            tenant_id=role.tenant_id
        )
        db.add(admin_user)
        await db.commit()
        await db.refresh(admin_user)
    return admin_user

async def _create_regular_user(db: AsyncSession, role: Role, level: Levels | None = None):
    """
    Creates a regular user if it does not exist.
    
    Args:
        db: The database session.
        role: The Role object (with UUID id).
        level: Optional Levels object (with UUID id).
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
            tenant_id=role.tenant_id
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
