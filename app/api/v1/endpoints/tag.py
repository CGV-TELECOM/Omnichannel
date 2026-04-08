from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.core.config.database import get_db
from app.schemas.requests.tag import TagCreate, TagUpdate
from app.db.models import User
from app.core.security.permissions import has_permission
from app.core.config.logging import log_user_action
from app.core.dependencies.dependencies import get_current_user_dependency
from app.services.v1 import handle_tag
from uuid import UUID

router = APIRouter(
    prefix="/tags",
    tags=["Tags"]
)


@router.get("")
async def get_tags(
    request: Request,
    id: Optional[UUID] = Query(None, description="ID của tag"),
    page: int = Query(1, ge=1, description="Số trang"),
    page_size: int = Query(10, ge=1, le=100, description="Số bản ghi mỗi trang"),
    search: Optional[str] = Query(None, description="Từ khóa tìm kiếm (tên hoặc mô tả)"),
    sort_by: Optional[str] = Query(None, description="Trường sắp xếp (name, created_at, updated_at)"),
    sort_order: str = Query("asc", description="Thứ tự sắp xếp (asc/desc)"),
    is_active: Optional[int] = Query(None, ge=0, le=1, description="Lọc theo trạng thái (0/1)"),
    tag_type: Optional[str] = Query(
        None,
        description="Lọc theo loại tag: 'ticket' hoặc 'customer' (nếu để trống thì lấy tất cả)",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_tags"))
):
    """
    Lấy danh sách tags với phân trang và tìm kiếm

    - **id**: Lấy tag cụ thể theo ID (nếu có)
    - **page**: Số trang (mặc định 1)
    - **page_size**: Số bản ghi mỗi trang (1-100, mặc định 10)
    - **search**: Tìm kiếm theo tên hoặc mô tả
    - **sort_by**: Sắp xếp theo trường (name, created_at, updated_at)
    - **sort_order**: Thứ tự sắp xếp (asc/desc)
    - **is_active**: Lọc theo trạng thái active (0/1)
    - **tag_type**: Lọc theo loại tag: 'ticket' hoặc 'customer'

    **Lưu ý**: User chỉ thấy tags thuộc tenant của mình (trừ super admin)
    """
    return await handle_tag.get_tags(
        db=db,
        id=id,
        current_user=current_user,
        page=page,
        page_size=page_size,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        is_active=is_active,
        tag_type=tag_type,
    )


@router.get("/{tag_id}")
async def get_tag_by_id(
    request: Request,
    tag_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_tag_by_id"))
):
    """
    Lấy thông tin chi tiết một tag theo ID
    
    - **tag_id**: UUID của tag cần xem
    
    **Lưu ý**: User chỉ xem được tag thuộc tenant của mình (trừ super admin)
    """
    return await handle_tag.get_tag_by_id(tag_id, db, current_user)


@router.post("")
@log_user_action("create_tag")
async def create_tag(
    tag_data: TagCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("create_tag")),
):
    """
    Tạo tag mới
    
    - **name**: Tên tag (bắt buộc, 1-100 ký tự)
    - **description**: Mô tả tag (tùy chọn, tối đa 500 ký tự)
    - **color**: Mã màu hex (tùy chọn, format: #RRGGBB)
    
    **Lưu ý**: 
    - Tag sẽ tự động được gán tenant_id từ user hiện tại
    - Tên tag phải unique trong cùng tenant
    """
    return await handle_tag.create_tag(tag_data, db, current_user)


@router.put("/{tag_id}")
@log_user_action("update_tag")
async def update_tag(
    tag_id: UUID,
    tag_data: TagUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("edit_tag")),
):
    """
    Cập nhật thông tin tag
    
    - **tag_id**: UUID của tag cần cập nhật
    - **name**: Tên tag mới (tùy chọn)
    - **description**: Mô tả mới (tùy chọn)
    - **color**: Mã màu hex mới (tùy chọn)
    - **is_active**: Trạng thái active (0/1, tùy chọn)
    
    **Lưu ý**: User chỉ cập nhật được tag thuộc tenant của mình (trừ super admin)
    """
    return await handle_tag.update_tag(tag_id, tag_data, db, current_user)


@router.delete("/{tag_id}")
@log_user_action("delete_tag")
async def delete_tag(
    tag_id: UUID,
    request: Request,
    hard_delete: bool = Query(False, description="Xóa vĩnh viễn (chỉ super admin)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("delete_tag")),
):
    """
    Xóa tag
    
    - **tag_id**: UUID của tag cần xóa
    - **hard_delete**: 
        - False (mặc định): Soft delete (set is_active = 0)
        - True: Hard delete (xóa vĩnh viễn khỏi DB, chỉ super admin)
    
    **Lưu ý**: 
    - Soft delete: User chỉ xóa được tag thuộc tenant của mình
    - Hard delete: Chỉ super admin mới có quyền
    """
    if hard_delete:
        return await handle_tag.hard_delete_tag(tag_id, db, current_user)
    else:
        return await handle_tag.soft_delete_tag(tag_id, db, current_user)


@router.patch("/{tag_id}/activate")
@log_user_action("activate_tag")
async def activate_tag(
    tag_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("edit_tag")),
):
    """
    Kích hoạt lại tag đã bị vô hiệu hóa (set is_active = 1)
    
    - **tag_id**: UUID của tag cần kích hoạt
    
    **Lưu ý**: User chỉ kích hoạt được tag thuộc tenant của mình (trừ super admin)
    """
    from app.schemas.requests.tag import TagUpdate
    tag_data = TagUpdate(is_active=1)
    return await handle_tag.update_tag(tag_id, tag_data, db, current_user)


@router.get("/statistics/summary")
async def get_tag_statistics(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_dependency),
    _ = Depends(has_permission("view_tags")),
):
    """
    Lấy thống kê tổng quan về tags.

    Trả về: total_tags, active_tags, inactive_tags, top_used_tags (top 10).
    Chỉ thống kê tags thuộc tenant của user (trừ super admin).
    """
    return await handle_tag.get_tag_statistics(db, current_user)
