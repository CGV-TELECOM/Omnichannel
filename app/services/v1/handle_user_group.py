from app.schemas.requests.user_group import UserGroupCreate, UserGroupDelete, UserGroupCreateList
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import User, Group, GroupUser
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.utils.helpers import is_platform_admin


async def _resolve_scoped_user_group(
    db: AsyncSession,
    current_user: User,
    user_id,
    group_id,
):
    """
    Trả (user, group, error_response).
    - User thường: user & group phải thuộc tenant của mình.
    - Super admin: user & group phải cùng một tenant với nhau.
    """
    is_super_admin = await is_platform_admin(current_user, db)

    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        return None, None, api_response(
            status=ResponseStatus.ERROR,
            message="Người dùng không tồn tại",
            data=None,
            status_code=ResponseStatusCode.NOT_FOUND,
        )

    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not group:
        return None, None, api_response(
            status=ResponseStatus.ERROR,
            message="Nhóm không tồn tại",
            data=None,
            status_code=ResponseStatusCode.NOT_FOUND,
        )

    if not is_super_admin:
        if user.tenant_id != current_user.tenant_id or group.tenant_id != current_user.tenant_id:
            return None, None, api_response(
                status=ResponseStatus.ERROR,
                message="Không được gán user/group thuộc tenant khác",
                data=None,
                status_code=ResponseStatusCode.FORBIDDEN,
            )
    elif user.tenant_id != group.tenant_id:
        return None, None, api_response(
            status=ResponseStatus.ERROR,
            message="User và group phải thuộc cùng một tenant",
            data=None,
            status_code=ResponseStatusCode.BAD_REQUEST,
        )

    return user, group, None


async def assign_users_to_groups(
    user_group_data: UserGroupCreateList,
    db: AsyncSession,
    current_user: User,
):
    try:
        for item in user_group_data.items:
            user, group, err = await _resolve_scoped_user_group(
                db, current_user, item.user_id, item.group_id
            )
            if err:
                await db.rollback()
                return err

            result = await db.execute(
                select(GroupUser).filter_by(user_id=item.user_id, group_id=item.group_id)
            )
            if result.scalar_one_or_none():
                await db.rollback()
                return api_response(
                    status=ResponseStatus.ERROR,
                    message="Người dùng đã được gán vào nhóm",
                    data=None,
                    status_code=ResponseStatusCode.CONFLICT,
                )

            db.add(
                GroupUser(
                    user_id=item.user_id,
                    group_id=item.group_id,
                    tenant_id=group.tenant_id or user.tenant_id,
                )
            )

        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Thêm người dùng vào nhóm thành công",
            data=None,
            status_code=ResponseStatusCode.OK,
        )

    except SQLAlchemyError as e:
        await db.rollback()
        print(f"DB error: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi cơ sở dữ liệu",
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
        )
    except Exception as e:
        await db.rollback()
        print(f"Unexpected error: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi không xác định",
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
        )


async def delete_user_group(
    user_group_data: UserGroupDelete,
    db: AsyncSession,
    current_user: User,
):
    try:
        user, group, err = await _resolve_scoped_user_group(
            db, current_user, user_group_data.user_id, user_group_data.group_id
        )
        if err:
            return err

        user_group_query = await db.execute(
            select(GroupUser).filter_by(
                user_id=user_group_data.user_id,
                group_id=user_group_data.group_id,
            )
        )
        user_group = user_group_query.scalar_one_or_none()

        if user_group is None:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Người dùng không được gán vào nhóm",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND,
            )

        await db.delete(user_group)
        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Người dùng đã được xóa khỏi nhóm",
            data=None,
            status_code=ResponseStatusCode.OK,
        )

    except Exception as e:
        await db.rollback()
        print(f"[ERROR] {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            message="Có lỗi xảy ra khi xóa người dùng khỏi nhóm",
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
        )
