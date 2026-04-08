from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response
from app.schemas.responses.api_response_rule import ResponseStatus, ResponseStatusCode
from app.db.models import Log
from sqlalchemy import select, func, update, and_

async def get_logs(page: int, page_size: int, search: str, day: str, month: str, year: str, db: AsyncSession):
    try:
        # Tính toán offset
        offset = (page - 1) * page_size
        
        # Tạo base query với các điều kiện tìm kiếm
        base_query = select(Log)
        if search:
            base_query = base_query.where(Log.user_name.ilike(f"%{search}%"))
        if day:
            base_query = base_query.where(func.date_part('day', Log.create_time) == int(day))
        if month:   
            base_query = base_query.where(func.date_part('month', Log.create_time) == int(month))
        if year:
            base_query = base_query.where(func.date_part('year', Log.create_time) == int(year))
        
        # Query để lấy dữ liệu với phân trang
        query = base_query.offset(offset).limit(page_size)
        result = await db.execute(query)
        logs = result.scalars().all()
        
        # Query để đếm tổng số bản ghi với các điều kiện tìm kiếm
        count_query = select(func.count()).select_from(base_query.subquery())
        total_records = await db.scalar(count_query) or 0
        
        # Tính toán tổng số trang
        total_pages = (total_records + page_size - 1) // page_size
        
        # Tạo phân trang
        paginated_logs = {
            "logs": logs,
            "total_pages": total_pages,
            "total_records": total_records
        }
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Lấy danh sách log thành công",
            data=paginated_logs,
            status_code=ResponseStatusCode.OK
        )
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(e),
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
