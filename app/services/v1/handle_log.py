from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response
from app.schemas.responses.api_response_rule import ResponseStatus, ResponseStatusCode
from app.db.models import Log, User
from sqlalchemy import select, func, and_, or_
from app.utils.helpers import is_platform_admin
from uuid import UUID
from typing import Optional


async def get_logs(
    page: int,
    page_size: int,
    search: str,
    day: str,
    month: str,
    year: str,
    db: AsyncSession,
    current_user: User,
    tenant_id: Optional[UUID] = None,
):
    try:
        offset = (page - 1) * page_size
        is_super_admin = await is_platform_admin(current_user, db)

        base_query = select(Log)
        filters = []

        # Tenant isolation
        if is_super_admin and tenant_id:
            filters.append(Log.tenant_id == tenant_id)
        elif not is_super_admin:
            # Log.tenant_id (mới) hoặc fallback qua user.tenant_id (log cũ thiếu tenant_id)
            filters.append(
                or_(
                    Log.tenant_id == current_user.tenant_id,
                    and_(
                        Log.tenant_id.is_(None),
                        Log.user_id.in_(
                            select(User.id).where(User.tenant_id == current_user.tenant_id)
                        ),
                    ),
                )
            )

        if search:
            filters.append(Log.user_name.ilike(f"%{search}%"))
        if day:
            filters.append(func.date_part("day", Log.create_time) == int(day))
        if month:
            filters.append(func.date_part("month", Log.create_time) == int(month))
        if year:
            filters.append(func.date_part("year", Log.create_time) == int(year))

        if filters:
            base_query = base_query.where(and_(*filters))

        query = base_query.order_by(Log.create_time.desc()).offset(offset).limit(page_size)
        result = await db.execute(query)
        logs = result.scalars().all()

        count_query = select(func.count()).select_from(base_query.subquery())
        total_records = await db.scalar(count_query) or 0
        total_pages = (total_records + page_size - 1) // page_size

        paginated_logs = {
            "logs": logs,
            "total_pages": total_pages,
            "total_records": total_records,
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Lấy danh sách log thành công",
            data=paginated_logs,
            status_code=ResponseStatusCode.OK,
        )
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(e),
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
        )
