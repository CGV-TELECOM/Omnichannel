from app.db.models import Levels, User
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import  func
from typing import Optional, cast
# Hàm check chung level max
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