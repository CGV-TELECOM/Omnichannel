from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config.database import get_db
from app.core.security.permissions import has_permission
from app.core.dependencies.dependencies import get_current_user_dependency
from app.db.models import User
from app.services.v1 import handle_log
from uuid import UUID
from typing import Optional


router = APIRouter(
    prefix="/logs",
    tags=["Logs"]
)


@router.get("")
async def get_logs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    search: str = Query(None, description="Từ khóa tìm kiếm"),
    day: str = Query(None, description="Ngày"),
    month: str = Query(None, description="Tháng"),
    year: str = Query(None, description="Năm"),
    tenant_id: Optional[UUID] = Query(None, description="Lọc tenant (chỉ Super Admin)"),
    _ = Depends(has_permission("view_logs")),
):
    return await handle_log.get_logs(
        page=page,
        page_size=page_size,
        search=search,
        day=day,
        month=month,
        year=year,
        db=db,
        current_user=current_user,
        tenant_id=tenant_id,
    )
