from app.db.models import Levels, User
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import  func
from typing import Optional, cast


async def get_global_max_level_order(db: AsyncSession) -> int:
    """level_order cao nhất trong catalog (hiện tại = Admin/CGV)."""
    stmt = select(func.max(Levels.level_order)).select_from(Levels)
    result = await db.execute(stmt)
    return cast(int, result.scalar_one_or_none() or 0)


async def is_platform_admin(current_user, db: AsyncSession | None = None) -> bool:
    """
    Platform admin (CGV ops): duy nhất nhóm này được thao tác cross-tenant.

    Thay thế cho isCheckMaxLevel ở các chỗ dùng để bypass tenant:
    admin của 1 tenant (level Admin trong tenant) KHÔNG còn được coi là super admin.
    Tham số db giữ lại để tương thích chữ ký với isCheckMaxLevel tại các call site.
    """
    return bool(getattr(current_user, "is_platform_admin", False))


# DEPRECATED: chỉ so max(level_order) TOÀN HỆ THỐNG nên admin tenant cũng thành
# "super admin" và bypass tenant. Dùng is_platform_admin (cross-tenant) hoặc
# isCheckMaxLevelTenant (cao nhất trong tenant) thay thế.
async def isCheckMaxLevel(current_user, db : AsyncSession):
    stmt = select(func.max(Levels.level_order)).select_from(Levels)
    result = await db.execute(stmt)
    max_level_order = cast(int, result.scalar_one_or_none() or 0)
    if current_user.level.level_order != max_level_order:
        return False
    return True 

async def isCheckMaxLevelTenant(current_user, db: AsyncSession) -> bool:
    # Kiểm tra level cao nhất trong tenant
    stmt_tenant = (
        select(func.max(Levels.level_order))
        .select_from(Levels)
        .join(User, User.level_id == Levels.id)
        .where(User.tenant_id == current_user.tenant_id)
    )
    
    result_tenant = await db.execute(stmt_tenant)
    max_tenant_level = cast(int, result_tenant.scalar_one_or_none() or 0)

    return current_user.level.level_order == max_tenant_level