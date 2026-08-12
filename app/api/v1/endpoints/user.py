from typing import Optional
from fastapi import APIRouter, Depends, Request, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from app.core.config.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.v1 import handle_user
from app.core.config.logging import log_user_action
from app.core.security.permissions import has_permission
from app.schemas.requests.user import CreateUserRequest, UpdateUserRequest
from app.db.models import User
from app.core.dependencies.dependencies import get_current_user_dependency
from uuid import UUID

router = APIRouter(prefix="/user", tags=["User"])


@router.get("/current")
async def get_current_user(
    request :  Request,
    db: AsyncSession = Depends(get_db),
    _ = Depends(has_permission("current_user"))
):
    return await handle_user.get_current_user_or_none(request, db)


@router.get("/webcall")
async def get_my_webcall(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("current_user")),
):
    """
    Lấy full config softphone (sip_password, api_key, ws_server...).
    FE chỉ gọi khi cần kết nối gọi — không cache.
    """
    body = await handle_user.get_my_webcall_config(current_user, db)
    return JSONResponse(
        content=jsonable_encoder(body),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, private",
            "Pragma": "no-cache",
        },
    )


@router.get("/all")
async def get_all_users(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của người dùng"),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm"),
    sort_by: Optional[str] = Query(None, description="Trường sắp xếp"),
    sort_order: str = Query("asc", description="Thứ tự sắp xếp (asc/desc)"),
    _ = Depends(has_permission("view_users")),
    current_user: User = Depends(get_current_user_dependency)
):
    return await handle_user.get_all_users(
        db=db,
        id=id,
        current_user=current_user,
        page=page,
        page_size=page_size,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order
    )

@router.get("/{user_id}")
async def get_user_by_id(
    request :  Request,
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    _ = Depends(has_permission("view_users")),
    current_user: User = Depends(get_current_user_dependency)
):
    return await handle_user.get_user_by_id(user_id, db, current_user)

@router.post("")
@log_user_action("create_user")
async def create_user(
    user_data: CreateUserRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ = Depends(has_permission("create_users")),
    current_user: User = Depends(get_current_user_dependency)
):
    return await handle_user.create_user(user_data, db, current_user)

@router.put("/{user_id}")
@log_user_action("update_user")
async def update_user(
    user_id: UUID,
    user_data: UpdateUserRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ = Depends(has_permission("edit_users")),
    current_user: User = Depends(get_current_user_dependency)
):
    return await handle_user.update_user(user_id, user_data, db, current_user)

@router.delete("/{user_id}")
@log_user_action("delete_user")
async def soft_delete_user(
    user_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ = Depends(has_permission("delete_users")),
    current_user: User = Depends(get_current_user_dependency)
):
    return await handle_user.soft_delete_user(user_id, db, current_user)


@router.post("/{user_id}/sync-chatwoot-agent")
@log_user_action("sync_chatwoot_agent")
async def sync_user_to_chatwoot_agent(
    user_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ = Depends(has_permission("edit_users")),
    current_user: User = Depends(get_current_user_dependency),
):
    return await handle_user.sync_user_to_chatwoot_agent(user_id, db, current_user)


# @router.get("/{user_id}/groups")
# async def get_user_groups(
#     user_id: int,
#     page: int = Query(1, ge=1, description="Số trang"),
#     page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
#     search: Optional[str] = Query(None, description="Từ khóa tìm kiếm"),
#     sort_by: Optional[str] = Query(None, description="Trường sắp xếp"),
#     sort_order: str = Query("asc", description="Thứ tự sắp xếp (asc/desc)"),
#     db: AsyncSession = Depends(get_db),
#     _ = Depends(has_permission("view_user_groups")),
#     current_user: User = Depends(get_current_user_dependency)
# ):
#     return await handle_user.get_user_groups(user_id, page, page_size, search, sort_by, sort_order, db, current_user)