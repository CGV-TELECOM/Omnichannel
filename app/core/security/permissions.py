from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError
from app.db.models import User, Permission, RolePermission, Role
from app.core.config.database import get_db
from app.core.security.jwt import (
    decode_access_token,
    _assert_token_version_valid,
)
from uuid import UUID


async def get_user_permissions(user_id: UUID, db: AsyncSession):
    try:
        # Chỉ lấy permission/role đang active (soft-disable phải có hiệu lực)
        stmt = (
            select(Permission.name)
            .join(RolePermission, Permission.id == RolePermission.permission_id)
            .join(Role, RolePermission.role_id == Role.id)
            .join(User, User.role_id == Role.id)
            .where(
                User.id == user_id,
                Role.is_active == 1,
                Permission.is_active == 1,
            )
        )
        result = await db.execute(stmt)
        permissions = [row[0] for row in result.all()]
        return permissions
    except SQLAlchemyError as e:
        print(f"Error getting user permissions: {str(e)}")
        return []
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return []


def has_permission(required_permission: str):
    async def dependency(
        request: Request,
        db: AsyncSession = Depends(get_db)
    ):
        # 1) Lấy token
        auth = request.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
        token = auth.split(" ", 1)[1]

        # 2) Decode payload (có token_version)
        payload = decode_access_token(token)
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
            )

        raw_id = payload.get("user_id")
        if raw_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
        user_id = UUID(raw_id) if isinstance(raw_id, str) else raw_id

        # 3) User + token_version + active
        user = await _assert_token_version_valid(db, user_id, payload.get("token_version"))

        # 4) Explicit query permissions (role + permission phải đang active)
        if user.role_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No role assigned")

        result = await db.execute(
            select(Permission.name)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .where(
                RolePermission.role_id == user.role_id,
                Role.is_active == 1,
                Permission.is_active == 1,
            )
        )
        perms = [r[0] for r in result.all()]

        # Role đã soft-delete → coi như không còn quyền
        if not perms:
            role = await db.get(Role, user.role_id)
            if role is None or role.is_active != 1:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Role is inactive or not found",
                )

        # 5) Check
        if required_permission not in perms:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    return dependency
