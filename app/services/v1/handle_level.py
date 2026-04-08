from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import Levels, User
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.requests.level import CreateLevelRequest, UpdateLevelRequest, LevelResponse
from uuid import UUID

async def get_levels(id : UUID | None, page: int, page_size: int, db: AsyncSession, current_user: User):
    try:
        if id:
            return await get_level_by_id(id, db, current_user)
        else:
            offset = (page - 1) * page_size

            # Get max level_order
            max_level_stmt = select(func.max(Levels.level_order)).select_from(Levels)
            max_level_result = await db.execute(max_level_stmt) 
            max_level_order = max_level_result.scalar_one_or_none() or 0

            # Get user's level_order
            user_level_order = 0
            if current_user.level_id is not None:
                user_level_stmt = select(Levels.level_order).where(Levels.id == current_user.level_id)
                user_level_result = await db.execute(user_level_stmt)
                user_level_order = user_level_result.scalar_one_or_none() or 0

            # Build query based on user's level
            base_query = select(Levels)
            if user_level_order != max_level_order:
                base_query = base_query.where(Levels.level_order <= user_level_order)

            # Add pagination
            paginated_query = base_query.offset(offset).limit(page_size)
            levels_result = await db.execute(paginated_query)
            levels = levels_result.scalars().all()

            # Get total count
            count_query = select(func.count()).select_from(Levels)
            if user_level_order != max_level_order:
                count_query = count_query.where(Levels.level_order <= user_level_order)
            count_result = await db.execute(count_query)
            total_records = count_result.scalar_one_or_none() or 0

            total_pages = (total_records + page_size - 1) // page_size

            return api_response(
                status=ResponseStatus.SUCCESS,
                message="Lấy danh sách cấp độ thành công",
                data={
                    "levels": [LevelResponse.model_validate(level) for level in levels],
                    "total_pages": total_pages,
                    "total_records": total_records
                },
                status_code=ResponseStatusCode.OK
            )
    except Exception as e:
        print(f"Error in get_levels: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(e),
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )

async def get_level_by_id(level_id: UUID, db: AsyncSession, current_user: User):
    try:
        # Lấy level_order của current user và max_level_order
        user_level_order = 0
        if current_user.level_id is not None:
            stmt = select(Levels.level_order).where(Levels.id == current_user.level_id)
            result = await db.execute(stmt)
            user_level_order = result.scalar_one_or_none() or 0

        stmt = select(func.max(Levels.level_order)).select_from(Levels)
        result = await db.execute(stmt)
        max_level_order = result.scalar_one_or_none() or 0

        # Lấy level được yêu cầu
        stmt = select(Levels).where(Levels.id == level_id)
        result = await db.execute(stmt)
        level = result.scalar_one_or_none()

        if not level:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Cấp độ không tồn tại",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND
            )

        # Kiểm tra quyền truy cập level
        stmt = select(Levels.level_order).where(Levels.id == level_id)
        result = await db.execute(stmt)
        level_order = result.scalar_one_or_none() or 0
        
        if user_level_order != max_level_order and level_order > user_level_order:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Bạn không có quyền xem cấp độ này",
                data=None,
                status_code=ResponseStatusCode.FORBIDDEN
            )
        level_data = LevelResponse.model_validate(level)
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Lấy thông tin cấp độ thành công",
            data=level_data,
            status_code=ResponseStatusCode.OK
        )
    except Exception as e:
        print(f"Error in get_level_by_id: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(e),
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )

# async def create_level(level_data: CreateLevelRequest, db: AsyncSession):
#     try:
#         # Kiểm tra xem cấp độ đã tồn tại chưa
#         existing_level = await db.scalar(
#             select(Levels).where(Levels.name == level_data.name)
#         )
#         if existing_level:
#             return api_response(
#                 status=ResponseStatus.ERROR,
#                 message="Cấp độ đã tồn tại",
#                 data=None,
#                 status_code=ResponseStatusCode.BAD_REQUEST
#             )
        
#         # Tạo cấp độ mới
#         new_level = Levels(
#             name=level_data.name,
#             description=level_data.description,
#         )
        
#         db.add(new_level)
#         await db.commit()
#         await db.refresh(new_level)

#         return api_response(
#             status=ResponseStatus.SUCCESS,
#             message="Tạo cấp độ thành công",
#             data=new_level,
#             status_code=ResponseStatusCode.CREATED
#         )
#     except Exception as e:
#         await db.rollback()
#         return api_response(
#             status=ResponseStatus.ERROR,
#             message=str(e),
#             data=None,
#             status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
#         )

# async def update_level(level_id: int, level_data: UpdateLevelRequest, db: AsyncSession):
#     try:
#         # Kiểm tra xem cấp độ có tồn tại không
#         level = await db.scalar(
#             select(Levels).where(Levels.id == level_id)
#         )
#         if not level:
#             return api_response(
#                 status=ResponseStatus.ERROR,
#                 message="Cấp độ không tồn tại",
#                 data=None,
#                 status_code=ResponseStatusCode.NOT_FOUND
#             )
        
#         # Kiểm tra xem tên cấp độ mới có trùng với cấp độ khác không
#         existing_level = await db.scalar(
#             select(Levels).where(Levels.name == level_data.name, Levels.id != level_id)
#         )
#         if existing_level:
#             return api_response(
#                 status=ResponseStatus.ERROR,
#                 message="Tên cấp độ đã tồn tại",
#                 data=None,
#                 status_code=ResponseStatusCode.BAD_REQUEST
#             )
        
#         # Cập nhật thông tin cấp độ
#         level.name = level_data.name
#         level.description = level_data.description

#         await db.commit()
#         await db.refresh(level)

#         return api_response(
#             status=ResponseStatus.SUCCESS,
#             message="Cập nhật cấp độ thành công",
#             data=level,
#             status_code=ResponseStatusCode.OK
#         )
#     except Exception as e:
#         await db.rollback()
#         return api_response(
#             status=ResponseStatus.ERROR,
#             message=str(e),
#             data=None,
#             status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
#         )

# async def delete_level(level_id: int, db: AsyncSession):
    # try:
    #     # Kiểm tra xem cấp độ có tồn tại không
    #     level = await db.scalar(
    #         select(Levels).where(Levels.id == level_id)
    #     )
    #     if not level:
    #         return api_response(
    #             status=ResponseStatus.ERROR,
    #             message="Cấp độ không tồn tại",
    #             data=None,
    #             status_code=ResponseStatusCode.NOT_FOUND
    #         )
        
    #     # Xóa cấp độ
    #     await db.delete(level)
    #     await db.commit()

    #     return api_response(
    #         status=ResponseStatus.SUCCESS,
    #         message="Xóa cấp độ thành công",
    #         data=None,
    #         status_code=ResponseStatusCode.OK
    #     )
    # except Exception as e:
    #     await db.rollback()
    #     return api_response(
    #         status=ResponseStatus.ERROR,
    #         message=str(e),
    #         data=None,
    #         status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
    #     )