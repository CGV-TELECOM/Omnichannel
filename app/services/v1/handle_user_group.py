from app.schemas.requests.user_group import UserGroupCreate, UserGroupDelete, UserGroupCreateList
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import User, Group, GroupUser
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

async def assign_users_to_groups(user_group_data: UserGroupCreateList, db: AsyncSession):
    try:
        for item in user_group_data.items:
            # Check user exists
            result = await db.execute(select(User).filter_by(id=item.user_id))
            user = result.scalar_one_or_none()
            if not user:
                raise ValueError(f"Người dùng không tồn tại")

            # Check group exists
            result = await db.execute(select(Group).filter_by(id=item.group_id))
            group = result.scalar_one_or_none()
            if not group:
                raise ValueError(f"Nhóm không tồn tại")

            # Check if already assigned
            result = await db.execute(
                select(GroupUser).filter_by(user_id=item.user_id, group_id=item.group_id)
            )
            if result.scalar_one_or_none():
                raise ValueError(f"Người dùng đã được gán vào nhóm")

            # Add new relation
            user_group = GroupUser(user_id=item.user_id, group_id=item.group_id)
            db.add(user_group)

        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Thêm người dùng vào nhóm thành công",
            data=None,
            status_code=ResponseStatusCode.OK
        )

    except ValueError as ve:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(ve),
            data=None,
            status_code=ResponseStatusCode.CONFLICT
        )
    except SQLAlchemyError as e:
        await db.rollback()
        print(f"DB error: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi cơ sở dữ liệu",
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
    except Exception as e:
        await db.rollback()
        print(f"Unexpected error: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi không xác định",
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )

async def delete_user_group(user_group_data: UserGroupDelete, db: AsyncSession):
    try:
        # check user_id and group_id is exist
        user_result = await db.execute(select(User).filter_by(id=user_group_data.user_id))
        group_result = await db.execute(select(Group).filter_by(id=user_group_data.group_id))
        user = user_result.scalar_one_or_none()
        group = group_result.scalar_one_or_none()

        if not user or not group:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Người dùng hoặc nhóm không tồn tại",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND
            )
        
        # check user_id and group_id is already assigned
        user_group_query = await db.execute(
            select(GroupUser).filter_by(user_id=user_group_data.user_id, group_id=user_group_data.group_id)
        )
        user_group = user_group_query.scalar_one_or_none()

        if user_group is None:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Người dùng không được gán vào nhóm",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND
            )

        # delete user from group
        await db.delete(user_group)
        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Người dùng đã được xóa khỏi nhóm",
            data=None,
            status_code=ResponseStatusCode.OK
        )

    except Exception as e:
        await db.rollback()
        print(f"[ERROR] {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Có lỗi xảy ra khi xóa người dùng khỏi nhóm",
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
