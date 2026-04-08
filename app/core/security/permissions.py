from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError
from jose import jwt, JWTError
from app.db.models import User, Permission, RolePermission, Role
from app.core.config.database import get_db
from app.core.config.app_config import settings
from app.core.security.jwt import get_user_id_from_token
from uuid import UUID

async def get_user_permissions(user_id: UUID, db: AsyncSession):
    try:
        # Truy vấn trực tiếp permissions từ database
        stmt = (
            select(Permission.name)
            .join(RolePermission, Permission.id == RolePermission.permission_id)
            .join(Role, RolePermission.role_id == Role.id)
            .join(User, User.role_id == Role.id)
            .where(User.id == user_id)
        )
        result = await db.execute(stmt)
        permissions = [row[0] for row in result.all()]
        print(f"Check result: {permissions}")
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

        # 2) Decode & lấy user_id
        user_id = get_user_id_from_token(token)
        # 3) Query user
        user = await db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
            
        is_active = await db.scalar(select(User.is_active).where(User.id == user_id))
        if is_active != 1:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive")

        # 4) Explicit query permissions
        role_id = await db.scalar(select(User.role_id).where(User.id == user_id))
        if role_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No role assigned")

        result = await db.execute(
            select(Permission.name)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
        )
        perms = [r[0] for r in result.all()]

        # 5) Check
        if required_permission not in perms:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    return dependency