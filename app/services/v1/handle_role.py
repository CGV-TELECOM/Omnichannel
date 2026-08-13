from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response
from app.schemas.responses.api_response_rule import ResponseStatus, ResponseStatusCode
from app.db.models import Role, User
from sqlalchemy import select, func, and_
from app.schemas.requests.role import CreateRoleRequest, UpdateRoleRequest, RoleResponse
from app.utils.helpers import is_platform_admin
from uuid import UUID


def _role_order(user: User) -> int:
    """role_order null-safe (cột nullable, tránh so sánh với None)."""
    if user.role is not None and user.role.role_order is not None:
        return user.role.role_order
    return 0


async def get_roles(
    id: UUID | None,
    page: int,
    page_size: int,
    search: str,
    sort_by: str,
    sort_order: str,
    db: AsyncSession,
    current_user: User
):
    """
    Role là catalog dùng chung (không chia theo tenant).
    - Platform admin: thấy tất cả role
    - Còn lại: chỉ thấy role active có role_order < của mình (được phép gán/thêm)
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)

        if id:
            return await get_role_by_id(id, current_user, is_super_admin, db)

        offset = (page - 1) * page_size
        filters = []
        if not is_super_admin:
            filters.append(Role.is_active == 1)
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
        filters = [Role.id == role_id]
        if not is_super_admin:
            filters.extend([
                Role.is_active == 1,
                Role.role_order < _role_order(current_user)
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
    Catalog role dùng chung — chỉ platform admin (CGV) được tạo.
    """
    try:
        if not await is_platform_admin(current_user, db):
            return api_response(
                status=ResponseStatus.ERROR,
                message="Chỉ platform admin mới được tạo vai trò trong catalog hệ thống",
                data=None,
                status_code=ResponseStatusCode.FORBIDDEN
            )

        result = await db.execute(
            select(Role).where(func.upper(Role.name) == role_data.name.upper())
        )
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
                db.add(existing_role)
                await db.commit()
                return api_response(
                    status=ResponseStatus.SUCCESS,
                    message="Tạo vai trò thành công",
                    data=None,
                    status_code=ResponseStatusCode.CREATED
                )

        new_role = Role(
            name=role_data.name,
            description=role_data.description,
            role_order=role_data.role_order
        )
        db.add(new_role)
        await db.commit()
        await db.refresh(new_role)

        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Tạo vai trò thành công",
            data=new_role,
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
        if not await is_platform_admin(current_user, db):
            return api_response(
                status=ResponseStatus.ERROR,
                message="Chỉ platform admin mới được cập nhật vai trò trong catalog hệ thống",
                data=None,
                status_code=ResponseStatusCode.FORBIDDEN
            )

        role = await db.scalar(select(Role).where(Role.id == role_id))
        if not role:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Vai trò không tồn tại",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND
            )

        existing_role = await db.scalar(
            select(Role).where(
                and_(
                    func.upper(Role.name) == role_data.name.upper(),
                    Role.id != role_id
                )
            )
        )
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
            data=role,
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
        if not await is_platform_admin(current_user, db):
            return api_response(
                status=ResponseStatus.ERROR,
                message="Chỉ platform admin mới được xóa vai trò trong catalog hệ thống",
                data=None,
                status_code=ResponseStatusCode.FORBIDDEN
            )

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
