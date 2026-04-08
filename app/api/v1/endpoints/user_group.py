from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config.database import get_db
from app.services.v1 import handle_user_group
from app.core.security.permissions import has_permission
from app.schemas.requests.user_group import UserGroupCreate, UserGroupDelete, UserGroupCreateList
from app.core.config.logging import log_user_action

router = APIRouter(
    prefix="/user_group",
    tags=["User Group"]
)


@router.post("/assign-multiple-users-to-groups")
@log_user_action("assign_users_to_groups")
async def assign_users_to_groups_endpoint(
    request: Request,
    user_group_data: UserGroupCreateList,
    db: AsyncSession = Depends(get_db),
    _ = Depends(has_permission("assign_user_to_group"))
):
    return await handle_user_group.assign_users_to_groups(user_group_data, db)


@router.delete("/remove-user-from-group")
@log_user_action("remove_user_from_group")
async def remove_user_from_group(
    request: Request,
    user_group_data: UserGroupDelete,
    db: AsyncSession = Depends(get_db),
    _ = Depends(has_permission("delete_user_group"))
):
    return await handle_user_group.delete_user_group(user_group_data, db)