from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Role, Permission, RolePermission, User, Levels, Tenant
from sqlalchemy.future import select
from uuid import UUID
import datetime

async def seed_specific_permission(db: AsyncSession):
    role_id = UUID("019b8bea-cc2d-7967-bce5-55008e6e286e")
    permission_name = "view_departments"

    # 1. Kiểm tra hoặc tạo Permission
    stmt_perm = select(Permission).filter_by(name=permission_name)
    result_perm = await db.execute(stmt_perm)
    permission = result_perm.scalar_one_or_none()

    if not permission:
        permission = Permission(
            name=permission_name,
            description="Quyền xem chi tiết phòng ban theo ID"
        )
        db.add(permission)
        await db.flush() # Lấy ID của permission mới mà chưa commit toàn bộ
    
    # 2. Kiểm tra xem Role có tồn tại không trước khi gán
    stmt_role = select(Role).filter_by(id=role_id)
    result_role = await db.execute(stmt_role)
    role = result_role.scalar_one_or_none()

    if role:
        # 3. Gán Permission cho Role (kiểm tra tồn tại trong bảng trung gian)
        stmt_link = select(RolePermission).filter_by(
            role_id=role_id, 
            permission_id=permission.id
        )
        result_link = await db.execute(stmt_link)
        link = result_link.scalar_one_or_none()

        if not link:
            new_link = RolePermission(
                role_id=role_id,
                permission_id=permission.id
            )
            db.add(new_link)
            await db.commit()
            print(f"Successfully assigned {permission_name} to role {role_id}")
        else:
            await db.rollback() # Tránh giữ transaction lâu
    else:
        print(f"Role ID {role_id} does not exist.")
