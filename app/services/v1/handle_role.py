from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response
from app.schemas.responses.api_response_rule import ResponseStatus, ResponseStatusCode
from app.db.models import Role, User, Tenant
from sqlalchemy import select, func, and_
from app.schemas.requests.role import CreateRoleRequest, UpdateRoleRequest, RoleResponse
from app.utils.helpers import is_platform_admin
from uuid import UUID


def _role_order(user: User) -> int:
    """role_order null-safe (cột nullable, tránh so sánh với None)."""
    if user.role is not None and user.role.role_order is not None:
        return user.role.role_order
    return 0


def _can_manage_tenant_role(current_user: User, role: Role) -> bool:
    """Tenant admin chỉ quản lý role cùng tenant và thấp hơn mình."""
    if role.tenant_id is None:
        return False
    if current_user.tenant_id is None:
        return False
    if role.tenant_id != current_user.tenant_id:
        return False
    return (role.role_order or 0) < _role_order(current_user)


async def _scope_tenant_id(
    current_user: User,
    is_super: bool,
    requested_tenant_id: UUID | None,
    db: AsyncSession,
):
    """
    Tenant admin: luôn tenant đang login (bỏ qua requested).
    Platform: requested_tenant_id hoặc tenant đang login.
    Trả (tenant_id, error_response).
    """
    if not is_super:
        if current_user.tenant_id is None:
            return None, api_response(
                status=ResponseStatus.ERROR,
                message="Tài khoản chưa thuộc tenant, không thể thao tác vai trò",
                data=None,
                status_code=ResponseStatusCode.FORBIDDEN,
            )
        return current_user.tenant_id, None

    target = requested_tenant_id or current_user.tenant_id
    if target is None:
        return None, api_response(
            status=ResponseStatus.ERROR,
            message="Cần chọn tenant để xem / tạo vai trò",
            data=None,
            status_code=ResponseStatusCode.BAD_REQUEST,
        )

    tenant = await db.scalar(
        select(Tenant).where(Tenant.id == target, Tenant.is_active == 1)
    )
    if not tenant:
        return None, api_response(
            status=ResponseStatus.ERROR,
            message="Tenant không tồn tại hoặc đã bị vô hiệu hóa",
            data=None,
            status_code=ResponseStatusCode.BAD_REQUEST,
        )
    return target, None


async def get_roles(
    id: UUID | None,
    page: int,
    page_size: int,
    search: str,
    sort_by: str,
    sort_order: str,
    db: AsyncSession,
    current_user: User,
    tenant_id: UUID | None = None,
):
    """
    Role theo tenant.
    - Tenant admin: chỉ tenant đang login
    - Platform: ?tenant_id= tenant đích; thiếu thì tenant đang login
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)

        if id:
            return await get_role_by_id(id, current_user, is_super_admin, db)

        scope_tenant_id, err = await _scope_tenant_id(
            current_user, is_super_admin, tenant_id, db
        )
        if err:
            return err

        offset = (page - 1) * page_size
        filters = [
            Role.tenant_id == scope_tenant_id,
            Role.is_active == 1,
        ]
        if not is_super_admin:
            filters.append(Role.role_order < _role_order(current_user))
        if search:
            filters.append(Role.name.ilike(f"%{search}%"))

        query = select(Role).where(*filters)

        if sort_by and hasattr(Role, sort_by):
            column = getattr(Role, sort_by)
            query = query.order_by(column.desc() if sort_order == "desc" else column.asc())

        query = query.offset(offset).limit(page_size)
        result = await db.execute(query)
        roles = result.scalars().all()

        count_query = select(func.count()).select_from(Role).where(*filters)
        total_records = await db.scalar(count_query) or 0
        total_pages = (total_records + page_size - 1) // page_size

        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Lấy danh sách vai trò thành công",
            data={
                "roles": [RoleResponse.model_validate(role) for role in roles],
                "total_pages": total_pages,
                "total_records": total_records
            },
            status_code=ResponseStatusCode.OK
        )

    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(e),
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )


async def get_role_by_id(role_id: UUID, current_user: User, is_super_admin: bool, db: AsyncSession):
    try:
        filters = [Role.id == role_id, Role.is_active == 1]
        if is_super_admin:
            filters.append(Role.tenant_id.isnot(None))
        else:
            filters.extend([
                Role.tenant_id == current_user.tenant_id,
                Role.role_order < _role_order(current_user),
            ])

        role = await db.scalar(select(Role).where(*filters))

        if not role:
            return api_response(
                status=ResponseStatus.INFO,
                message="Không tìm thấy vai trò này",
                status_code=ResponseStatusCode.BAD_REQUEST
            )
        role_data = RoleResponse.model_validate(role)
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Lấy vai trò thành công",
            data=role_data,
            status_code=ResponseStatusCode.OK
        )

    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(e),
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )


async def create_role(role_data: CreateRoleRequest, current_user: User, db: AsyncSession):
    """
    Tạo role trong tenant scope.
    - Tenant admin: tenant đang login
    - Platform: body.tenant_id hoặc tenant đang login
    Role platform `admin` (tenant_id NULL) chỉ do seed.
    """
    try:
        is_super = await is_platform_admin(current_user, db)

        requested = role_data.tenant_id if is_super else None
        target_tenant_id, err = await _scope_tenant_id(
            current_user, is_super, requested, db
        )
        if err:
            return err

        if not is_super and _role_order(current_user) <= role_data.role_order:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Bạn chỉ có thể tạo role nhỏ hơn role_order của mình",
                data=None,
                status_code=ResponseStatusCode.FORBIDDEN,
            )

        name_filter = and_(
            func.upper(Role.name) == role_data.name.upper(),
            Role.tenant_id == target_tenant_id,
        )

        result = await db.execute(select(Role).where(name_filter))
        existing_role = result.scalar_one_or_none()

        if existing_role:
            if existing_role.is_active == 1:
                return api_response(
                    status=ResponseStatus.ERROR,
                    message="Vai trò đã tồn tại",
                    data=None,
                    status_code=ResponseStatusCode.BAD_REQUEST
                )
            else:
                existing_role.description = role_data.description
                existing_role.role_order = role_data.role_order
                existing_role.is_active = 1
                existing_role.tenant_id = target_tenant_id
                db.add(existing_role)
                await db.commit()
                return api_response(
                    status=ResponseStatus.SUCCESS,
                    message="Tạo vai trò thành công",
                    data=RoleResponse.model_validate(existing_role),
                    status_code=ResponseStatusCode.CREATED
                )

        new_role = Role(
            name=role_data.name,
            description=role_data.description,
            role_order=role_data.role_order,
            tenant_id=target_tenant_id,
        )
        db.add(new_role)
        await db.commit()
        await db.refresh(new_role)

        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Tạo vai trò thành công",
            data=RoleResponse.model_validate(new_role),
            status_code=ResponseStatusCode.CREATED
        )

    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(e),
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )


async def update_role(
    role_id: UUID,
    role_data: UpdateRoleRequest,
    current_user: User,
    db: AsyncSession
):
    try:
        is_super = await is_platform_admin(current_user, db)

        role = await db.scalar(select(Role).where(Role.id == role_id))
        if not role:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Vai trò không tồn tại",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND
            )

        if role.tenant_id is None:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Không thể cập nhật role platform qua API này",
                data=None,
                status_code=ResponseStatusCode.FORBIDDEN,
            )

        if not is_super:
            if role.tenant_id != current_user.tenant_id:
                return api_response(
                    status=ResponseStatus.ERROR,
                    message="Bạn chỉ có thể cập nhật role trong tenant của mình",
                    data=None,
                    status_code=ResponseStatusCode.FORBIDDEN,
                )
            if not _can_manage_tenant_role(current_user, role):
                return api_response(
                    status=ResponseStatus.ERROR,
                    message="Bạn chỉ có thể cập nhật role trong tenant của mình và thấp hơn vai trò của bạn",
                    data=None,
                    status_code=ResponseStatusCode.FORBIDDEN
                )
            if role_data.role_order is not None and _role_order(current_user) <= role_data.role_order:
                return api_response(
                    status=ResponseStatus.ERROR,
                    message="Bạn chỉ có thể cập nhật role nhỏ hơn role_order của mình",
                    data=None,
                    status_code=ResponseStatusCode.BAD_REQUEST
                )

        name_filter = and_(
            func.upper(Role.name) == role_data.name.upper(),
            Role.tenant_id == role.tenant_id,
            Role.id != role_id,
        )

        existing_role = await db.scalar(select(Role).where(name_filter))
        if existing_role and existing_role.is_active == 1:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Tên vai trò đã tồn tại trong hệ thống",
                data=None,
                status_code=ResponseStatusCode.BAD_REQUEST
            )

        role.name = role_data.name
        role.description = role_data.description
        role.is_active = role_data.is_active
        role.role_order = role_data.role_order

        await db.commit()
        await db.refresh(role)

        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Cập nhật vai trò thành công",
            data=RoleResponse.model_validate(role),
            status_code=ResponseStatusCode.OK
        )

    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(e),
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )


async def delete_role(role_id: UUID, current_user: User, db: AsyncSession):
    try:
        is_super = await is_platform_admin(current_user, db)
        role = await db.scalar(
            select(Role).where(and_(Role.id == role_id, Role.is_active == 1))
        )

        if not role:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Vai trò không tồn tại",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND
            )

        if role.tenant_id is None:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Không thể xóa role platform",
                data=None,
                status_code=ResponseStatusCode.FORBIDDEN,
            )

        if not is_super:
            if role.tenant_id != current_user.tenant_id:
                return api_response(
                    status=ResponseStatus.ERROR,
                    message="Bạn chỉ có thể xóa role trong tenant của mình",
                    data=None,
                    status_code=ResponseStatusCode.FORBIDDEN,
                )
            if not _can_manage_tenant_role(current_user, role):
                return api_response(
                    status=ResponseStatus.ERROR,
                    message="Bạn chỉ có thể xóa role trong tenant của mình và thấp hơn vai trò của bạn",
                    data=None,
                    status_code=ResponseStatusCode.FORBIDDEN
                )

        user_using_role = await db.scalar(
            select(User).where(User.role_id == role_id)
        )

        if user_using_role:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Không thể xóa vai trò đang có người dùng được sử dụng",
                data=None,
                status_code=ResponseStatusCode.BAD_REQUEST
            )

        role.is_active = 0
        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Xóa vai trò thành công",
            data=None,
            status_code=ResponseStatusCode.OK
        )

    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(e),
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
