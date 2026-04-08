from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import Role, Permission, RolePermission
from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func
from app.db.models import User
from typing import Optional, List, Union
from uuid import UUID
from app.utils.helpers import isCheckMaxLevel
from app.schemas.requests.role_permission import AssignPermissionsRequest
from collections import defaultdict

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
    try:
        user_max_level = await isCheckMaxLevel(current_user, db)

        # 1️⃣ Check role
        if user_max_level:
            role = await db.get(Role, role_id)
        else:
            stmt = select(Role).where(
                Role.id == role_id,
                Role.tenant_id == current_user.tenant_id
            )
            result = await db.execute(stmt)
            role = result.scalar_one_or_none()

        if not role:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Vai trò không tồn tại hoặc không thuộc tenant của bạn"
            )

        # 2️⃣ Lấy permissions
        stmt = (
            select(Permission)
            .join(RolePermission, Permission.id == RolePermission.permission_id)
            .where(RolePermission.role_id == role_id)
        )

        if not user_max_level:
            stmt = stmt.where(
                Permission.tenant_id == current_user.tenant_id,
                Permission.is_active == 1
            )

        result = await db.execute(stmt)
        permissions = result.scalars().all()
        count = len(permissions)

        # 3️⃣ Group theo ACTION, model lấy từ belong_to
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
    tenant_id: Optional[UUID] = None
):
    try:
        user_max_level = await isCheckMaxLevel(current_user, db)

        # Nếu có tenant_id nhưng không phải max level thì chặn
        if tenant_id and not user_max_level:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Bạn không có quyền chỉ định tenant"
            )

        # Dùng tenant_id từ body nếu có max level, ngược lại dùng của current_user
        tenant_to_use = tenant_id or current_user.tenant_id

        stmt = select(Role).where(
            and_(
                Role.id == role_id,
                Role.tenant_id == tenant_to_use
            )
        )
        result = await db.execute(stmt)
        role = result.scalar_one_or_none()

        if not role:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Role không tồn tại hoặc không thuộc tenant"
            )

        # Kiểm tra từng permission theo đúng tenant
        for permission_id in permission_ids:
            stmt = select(Permission).where(
                and_(
                    Permission.id == permission_id,
                    Permission.tenant_id == tenant_to_use
                )
            )
            result = await db.execute(stmt)
            permission = result.scalar_one_or_none()

            if not permission:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.NOT_FOUND,
                    message=f"Quyền với ID {permission_id} không tồn tại hoặc không thuộc tenant"
                )

        # Xóa quyền cũ
        await db.execute(
            delete(RolePermission).where(RolePermission.role_id == role_id)
        )

        # Thêm quyền mới
        for permission_id in permission_ids:
            db.add(RolePermission(
                role_id=role_id,
                permission_id=permission_id,
                tenant_id=tenant_to_use 
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
    tenant_id: Optional[UUID] = None
):
    try:
        user_max_level = await isCheckMaxLevel(current_user, db)

        # Nếu có tenant_id nhưng không phải max level thì chặn
        if tenant_id and not user_max_level:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Bạn không có quyền chỉ định tenant"
            )

        # Dùng tenant_id từ body nếu có max level, ngược lại dùng của current_user
        tenant_to_use = tenant_id or current_user.tenant_id

        stmt = select(Role).where(
            and_(
                Role.id == role_id,
                Role.tenant_id == tenant_to_use
            )
        )
        result = await db.execute(stmt)
        role = result.scalar_one_or_none()

        if not role:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Vai trò không tồn tại hoặc không thuộc tenant"
            )

         # Kiểm tra permission tồn tại và thuộc tenant
        stmt_permission = select(Permission).where(
            and_(
                Permission.id == permission_id,
                Permission.tenant_id == tenant_to_use
            )
        )
        result_permission = await db.execute(stmt_permission)
        permission = result_permission.scalar_one_or_none()

        if not permission:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Quyền không tồn tại hoặc không thuộc tenant"
            )

        # Kiểm tra role có quyền này không
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


        # Xóa quyền
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