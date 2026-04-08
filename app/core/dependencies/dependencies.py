from fastapi import Request, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config.database import get_db
from app.services.v1.handle_user import get_current_user
from app.db.models import User
from sqlalchemy.future import select


async def get_current_user_dependency(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User:
    return await get_current_user(request, db) 
