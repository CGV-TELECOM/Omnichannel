from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode

from app.db.models import Permission, RolePermission
from sqlalchemy import select, update, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import or_
from sqlalchemy import func

from collections import defaultdict
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from app.utils.helpers import is_platform_admin
from app.db.models import User
from app.schemas.requests.permission import CreatePermissionTenantRequest
from app.seeds.rbac import infer_permission_belong_to
from uuid import UUID


async def get_permissions(
    db: AsyncSession,
    current_user: User,
    search: str | None = None,
    id: UUID | None = None,
    for_assign: bool = False,
):
    try:
        if id:
            return await get_permission_by_id(id, db, current_user)

        # Permission là catalog dùng chung (không chia theo tenant).
        # Non-platform chỉ thấy permission đang active.
        # for_assign=True: chỉ quyền thuộc role của caller (dùng khi gán cho role khác).
        user_max_level = await is_platform_admin(current_user, db)

        if for_assign and not current_user.role_id:
            return api_response(
                status=ResponseStatus.SUCCESS,
                status_code=ResponseStatusCode.OK,
                message="Lấy danh sách quyền thành công (Tổng: 0 quyền)",
                data={},
            )

        query = select(Permission)
        if for_assign:
            query = (
                query.join(
                    RolePermission,
                    Permission.id == RolePermission.permission_id,
                )
                .where(RolePermission.role_id == current_user.role_id)
            )
            # Khi gán quyền chỉ lấy perm active (kể cả platform admin)
            query = query.where(Permission.is_active == 1)
        elif not user_max_level:
            query = query.where(Permission.is_active == 1)

        if search:
            search_pattern = f"%{search}%"
            query = query.where(
                or_(
                    Permission.name.ilike(search_pattern),
                    Permission.description.ilike(search_pattern)
                )
            )

        result = await db.execute(query)
        permissions = result.scalars().unique().all()
        count = len(permissions)

        # ===============================
        # GROUP THEO PREFIX (TRƯỚC "_")
        # ===============================
        grouped_permissions = defaultdict(list)

        for permission in permissions:
            permission_name = permission.name

            # luôn tách trước dấu "_"
            action = permission_name.split("_", 1)[0]

            grouped_permissions[action].append({
                "id": permission.id,
                "name": permission.name,
                "description": permission.description,
                "belong_to": permission.belong_to
            })

        msg_suffix = " (chỉ quyền có thể gán)" if for_assign else ""
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message=(
                f"Lấy danh sách quyền thành công{msg_suffix}"
                + (f" (Tổng: {count} quyền)" if count > 0 else "")
            ),
            data=grouped_permissions
        )

    except SQLAlchemyError as e:
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )


async def get_permission_by_id(permission_id: UUID, db: AsyncSession, current_user: User):
    try:
        user_max_level = await is_platform_admin(current_user, db)

        if user_max_level:
            stmt = select(Permission).where(
                Permission.id == permission_id
            )
        else:
            stmt = select(Permission).where(
                Permission.id == permission_id,
                Permission.is_active == 1
            )

        result = await db.execute(stmt)
        permission = result.scalar_one_or_none()

        if not permission:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Quyền không tồn tại"
            )

        permission_data = {
            "id": permission.id,
            "name": permission.name,
            "description": permission.description
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin quyền thành công",
            data=permission_data
        )

    except SQLAlchemyError as e:
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )


async def create_tenant_permission(
    request_data: CreatePermissionTenantRequest,
    db: AsyncSession,
    current_user: User
):
    """Bulk create permissions trong catalog dùng chung (chỉ platform admin)."""
    try:
        user_level_max = await is_platform_admin(current_user, db)

        if not user_level_max:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Tài khoản không có quyền tạo",
            )

        permissions = []

        for item in request_data.permissions:
            existing_stmt = select(Permission).where(Permission.name == item.name)
            existing = await db.execute(existing_stmt)
            if existing.scalar():
                continue

            new_permission = Permission(
                name=item.name,
                description=item.description,
                belong_to=infer_permission_belong_to(item.name),
            )
            db.add(new_permission)
            permissions.append(new_permission)

        if not permissions:
            return api_response(
                status=ResponseStatus.WARNING,
                status_code=ResponseStatusCode.CONFLICT,
                message="Tất cả quyền đã tồn tại"
            )

        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message=f"Đã tạo {len(permissions)} quyền"
        )

    except SQLAlchemyError as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )

async def update_permission(permission_id: UUID, permission_data, db: AsyncSession, current_user: User):
    try:
        user_level_max = await is_platform_admin(current_user, db)

        if not user_level_max:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Tài khoản không có quyền tạo",
            )

        stmt = select(Permission).where(
            Permission.id == permission_id
        )
        result = await db.execute(stmt)
        permission = result.scalar_one_or_none()
        
        if not permission:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Quyền không tồn tại"
            )

        if permission_data.name and permission_data.name != permission.name:
            stmt = select(Permission).where(
                Permission.name == permission_data.name,
                Permission.id != permission.id
            )
            result = await db.execute(stmt)
            if result.scalar_one_or_none():
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.CONFLICT,
                    message="Tên quyền đã tồn tại"
                )

        update_data = permission_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(permission, key, value)

        await db.commit()
        await db.refresh(permission)

        permission_response = {
            "id": permission.id,
            "name": permission.name,
            "description": permission.description
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật quyền thành công",
            data=permission_response
        )

    except SQLAlchemyError as e:
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )

async def delete_permission(permission_id: UUID, db: AsyncSession, current_user: User):
    try:
        user_level_max = await is_platform_admin(current_user, db)
        if not user_level_max:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Tài khoản không có quyền xóa",
            )

        stmt = select(Permission).where(
            Permission.id == permission_id
        )
        result = await db.execute(stmt)
        permission = result.scalar_one_or_none()
        
        if not permission:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Quyền không tồn tại"
            )
        
        stmt = select(RolePermission).where(
            RolePermission.permission_id == permission_id,
        )
        result = await db.execute(stmt)
        if result.scalar_one_or_none():
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.CONFLICT,
                message="Quyền đang được sử dụng trong role"
            )
        
        await db.execute(
            update(Permission)
            .where(Permission.id == permission_id)
            .values(is_active=0)
        )
        await db.execute(
            delete(RolePermission).where(
                RolePermission.permission_id == permission_id,
            )
        )
        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa quyền thành công"
        )

    except SQLAlchemyError as e:
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )