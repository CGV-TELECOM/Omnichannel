from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import Role, Permission, RolePermission
from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from app.db.models import User
from uuid import UUID
from app.utils.helpers import is_platform_admin
from collections import defaultdict


def _role_order(user: User) -> int:
    """role_order null-safe (cột nullable, tránh so sánh với None)."""
    if user.role is not None and user.role.role_order is not None:
        return user.role.role_order
    return 0


def group_permissions_by_action(permissions):
    grouped = defaultdict(list)

    for permission in permissions:
        if "_" not in permission.name:
            continue

        action = permission.name.split("_", 1)[0]

        grouped[action].append({
            "id": permission.id,
            "name": permission.name,
            "description": permission.description,
            "belong_to": permission.belong_to
        })

    return grouped


async def get_role_permissions(
    role_id: UUID,
    db: AsyncSession,
    current_user: User
):
    """
    Role/permission là catalog dùng chung.
    Non-platform chỉ xem được quyền của role có order thấp hơn mình.
    """
    try:
        is_super = await is_platform_admin(current_user, db)

        role = await db.get(Role, role_id)
        if not role:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Vai trò không tồn tại"
            )

        if not is_super and (role.role_order or 0) >= _role_order(current_user):
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Bạn chỉ có thể xem quyền của vai trò thấp hơn vai trò của bạn"
            )

        stmt = (
            select(Permission)
            .join(RolePermission, Permission.id == RolePermission.permission_id)
            .where(RolePermission.role_id == role_id)
        )
        if not is_super:
            stmt = stmt.where(Permission.is_active == 1)

        result = await db.execute(stmt)
        permissions = result.scalars().all()
        count = len(permissions)

        grouped_permissions = group_permissions_by_action(permissions)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách quyền của vai trò thành công" + (f" (Tổng: {count} quyền)" if count > 0 else ""),
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


async def assign_permissions_to_role(
    role_id: UUID,
    permission_ids: list[UUID],
    db: AsyncSession,
    current_user: User,
):
    """
    Gán permission cho role trong catalog dùng chung.
    Chỉ platform admin (CGV) được thao tác — tránh tenant admin đổi quyền toàn hệ thống.
    """
    try:
        if not await is_platform_admin(current_user, db):
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Chỉ platform admin mới được gán quyền cho vai trò trong catalog hệ thống",
            )

        role = await db.get(Role, role_id)
        if not role:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Role không tồn tại"
            )

        for permission_id in permission_ids:
            stmt = select(Permission).where(Permission.id == permission_id)
            result = await db.execute(stmt)
            permission = result.scalar_one_or_none()

            if not permission:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.NOT_FOUND,
                    message=f"Quyền với ID {permission_id} không tồn tại"
                )

        await db.execute(
            delete(RolePermission).where(RolePermission.role_id == role_id)
        )

        for permission_id in permission_ids:
            db.add(RolePermission(
                role_id=role_id,
                permission_id=permission_id,
            ))
        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Gán quyền cho vai trò thành công"
        )

    except SQLAlchemyError as e:
        await db.rollback()
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        await db.rollback()
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )


async def remove_permission_from_role(
    role_id: UUID,
    permission_id: UUID,
    current_user: User,
    db: AsyncSession,
):
    try:
        if not await is_platform_admin(current_user, db):
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Chỉ platform admin mới được gỡ quyền khỏi vai trò trong catalog hệ thống",
            )

        role = await db.get(Role, role_id)
        if not role:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Vai trò không tồn tại"
            )

        stmt_permission = select(Permission).where(Permission.id == permission_id)
        result_permission = await db.execute(stmt_permission)
        permission = result_permission.scalar_one_or_none()

        if not permission:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Quyền không tồn tại"
            )

        stmt = select(RolePermission).where(
            and_(
                RolePermission.role_id == role_id,
                RolePermission.permission_id == permission_id
            )
        )
        result = await db.execute(stmt)
        role_permission = result.scalar_one_or_none()

        if not role_permission:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Vai trò không có quyền này"
            )

        await db.delete(role_permission)
        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa quyền khỏi vai trò thành công"
        )

    except SQLAlchemyError as e:
        await db.rollback()
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        await db.rollback()
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )
