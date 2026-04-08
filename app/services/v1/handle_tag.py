from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import Tag, User, Tenant, TagType, ticket_tag_association
from sqlalchemy import select, func, or_, and_
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from app.schemas.requests.tag import TagCreate, TagUpdate
from app.utils.helpers import isCheckMaxLevel, isCheckMaxLevelTenant
from uuid import UUID
from datetime import datetime, timezone


async def get_tags(
    db: AsyncSession,
    current_user: User,
    id: UUID | None = None,
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc",
    is_active: int | None = None,
    tag_type: str | None = None,
):
    """
    Lấy danh sách tags với phân trang, tìm kiếm và filter theo tenant
    """
    try:
        # Nếu có ID cụ thể, trả về tag đó
        if id:
            return await get_tag_by_id(id, db, current_user)

        # Check quyền super admin (level cao nhất)
        max_level_user = await isCheckMaxLevel(current_user, db)

        # Check tenant active
        if not max_level_user:
            tenant = await db.scalar(
                select(Tenant).where(
                    Tenant.id == current_user.tenant_id,
                    Tenant.is_active == 1
                )
            )
            if not tenant:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.FORBIDDEN,
                    message="Tenant không tồn tại hoặc đã bị vô hiệu hóa"
                )

        # Query base
        base_query = select(Tag)

        # Filter theo tenant - user chỉ thấy tags của tenant mình
        if not max_level_user:
            base_query = base_query.where(Tag.tenant_id == current_user.tenant_id)

        # Filter theo trạng thái active
        if is_active is not None:
            base_query = base_query.where(Tag.is_active == is_active)
        elif not max_level_user:
            # Nếu không phải super admin, mặc định chỉ hiển thị active tags
            base_query = base_query.where(Tag.is_active == 1)

        # Filter theo type (ticket / customer) nếu có
        if tag_type is not None:
            type_str = tag_type.strip().lower()
            if type_str in {"ticket", "customer"}:
                wanted_type = TagType.TICKET if type_str == "ticket" else TagType.CUSTOMER
                base_query = base_query.where(Tag.type == wanted_type)

        # Filter search (tìm theo tên hoặc mô tả)
        if search:
            like_search = f"%{search}%"
            base_query = base_query.where(
                or_(
                    Tag.name.ilike(like_search),
                    Tag.description.ilike(like_search)
                )
            )

        # Sort
        if sort_by and hasattr(Tag, sort_by):
            sort_col = getattr(Tag, sort_by)
            base_query = base_query.order_by(
                sort_col.desc() if sort_order.lower() == "desc" else sort_col.asc()
            )
        else:
            # Mặc định sắp xếp theo tên
            base_query = base_query.order_by(Tag.name.asc())

        # Count total
        count_query = select(func.count()).select_from(base_query.subquery())
        total_count = await db.scalar(count_query) or 0
        total_pages = (total_count + page_size - 1) // page_size

        # Pagination
        base_query = base_query.offset((page - 1) * page_size).limit(page_size)

        # Execute
        results = await db.execute(base_query)
        tags = results.scalars().all()

        # Format data
        tag_list = [
            {
                "id": str(tag.id),
                "name": tag.name,
                "description": tag.description,
                "color": tag.color,
                "type": tag.type.value if hasattr(tag.type, "value") else str(tag.type),
                "created_at": tag.created_at,
                "updated_at": tag.updated_at,
                "tenant_id": str(tag.tenant_id) if tag.tenant_id else None,
                "is_active": tag.is_active,
            }
            for tag in tags
        ]

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách tags thành công",
            data={
                "tags": tag_list,
                "pagination": {
                    "current_page": page,
                    "page_size": page_size,
                    "total_count": total_count,
                    "total_pages": total_pages
                }
            }
        )

    except SQLAlchemyError as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def get_tag_by_id(tag_id: UUID, db: AsyncSession, current_user: User):
    """
    Lấy thông tin chi tiết một tag theo ID
    """
    try:
        # Check quyền super admin
        max_level_user = await isCheckMaxLevel(current_user, db)

        # Query tag
        query = select(Tag).where(Tag.id == tag_id)
        
        # Nếu không phải super admin, chỉ xem được tag của tenant mình
        if not max_level_user:
            query = query.where(Tag.tenant_id == current_user.tenant_id)

        tag = await db.scalar(query)

        if not tag:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Tag không tồn tại hoặc bạn không có quyền truy cập"
            )

        tag_data = {
            "id": str(tag.id),
            "name": tag.name,
            "description": tag.description,
            "color": tag.color,
            "type": tag.type.value if hasattr(tag.type, "value") else str(tag.type),
            "created_at": tag.created_at,
            "updated_at": tag.updated_at,
            "tenant_id": str(tag.tenant_id) if tag.tenant_id else None,
            "is_active": tag.is_active,
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin tag thành công",
            data=tag_data
        )

    except SQLAlchemyError as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def create_tag(tag_data: TagCreate, db: AsyncSession, current_user: User):
    """
    Tạo tag mới - tự động gán tenant_id từ current_user
    """
    try:
        # Check tenant active
        if current_user.tenant_id:
            tenant = await db.scalar(
                select(Tenant).where(
                    Tenant.id == current_user.tenant_id,
                    Tenant.is_active == 1
                )
            )
            if not tenant:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.FORBIDDEN,
                    message="Tenant không tồn tại hoặc đã bị vô hiệu hóa"
                )

        # Check trùng tên tag trong cùng tenant
        existing_tag = await db.scalar(
            select(Tag).where(
                Tag.name == tag_data.name,
                Tag.tenant_id == current_user.tenant_id
            )
        )
        
        if existing_tag:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message=f"Tag với tên '{tag_data.name}' đã tồn tại trong hệ thống"
            )

        # Xác định type
        tag_type_enum = TagType.TICKET if tag_data.type == "ticket" else TagType.CUSTOMER

        # Tạo tag mới
        new_tag = Tag(
            name=tag_data.name,
            description=tag_data.description,
            color=tag_data.color,
            type=tag_type_enum,
            tenant_id=current_user.tenant_id,
            is_active=1,
        )

        db.add(new_tag)
        await db.commit()
        await db.refresh(new_tag)

        tag_response = {
            "id": str(new_tag.id),
            "name": new_tag.name,
            "description": new_tag.description,
            "color": new_tag.color,
            "type": new_tag.type.value if hasattr(new_tag.type, "value") else str(new_tag.type),
            "created_at": new_tag.created_at,
            "updated_at": new_tag.updated_at,
            "tenant_id": str(new_tag.tenant_id) if new_tag.tenant_id else None,
            "is_active": new_tag.is_active,
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message="Tạo tag thành công",
            data=tag_response
        )

    except IntegrityError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.BAD_REQUEST,
            message="Tag với tên này đã tồn tại",
            data=str(e)
        )
    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def update_tag(tag_id: UUID, tag_data: TagUpdate, db: AsyncSession, current_user: User):
    """
    Cập nhật thông tin tag
    """
    try:
        # Check quyền super admin
        max_level_user = await isCheckMaxLevel(current_user, db)

        # Query tag
        query = select(Tag).where(Tag.id == tag_id)
        
        # Nếu không phải super admin, chỉ update được tag của tenant mình
        if not max_level_user:
            query = query.where(Tag.tenant_id == current_user.tenant_id)

        tag = await db.scalar(query)

        if not tag:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Tag không tồn tại hoặc bạn không có quyền cập nhật"
            )

        # Check trùng tên nếu đổi tên
        if tag_data.name and tag_data.name != tag.name:
            existing_tag = await db.scalar(
                select(Tag).where(
                    Tag.name == tag_data.name,
                    Tag.tenant_id == current_user.tenant_id,
                    Tag.id != tag_id
                )
            )
            
            if existing_tag:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.BAD_REQUEST,
                    message=f"Tag với tên '{tag_data.name}' đã tồn tại"
                )

        # Update các field
        update_data = tag_data.model_dump(exclude_unset=True)

        # Xử lý riêng field type (string -> TagType)
        type_str = update_data.pop("type", None)
        if type_str is not None:
            type_str = type_str.strip().lower()
            if type_str in {"ticket", "customer"}:
                tag.type = TagType.TICKET if type_str == "ticket" else TagType.CUSTOMER

        for key, value in update_data.items():
            if hasattr(tag, key):
                setattr(tag, key, value)

        # Update timestamp
        tag.updated_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(tag)

        tag_response = {
            "id": str(tag.id),
            "name": tag.name,
            "description": tag.description,
            "color": tag.color,
            "type": tag.type.value if hasattr(tag.type, "value") else str(tag.type),
            "created_at": tag.created_at,
            "updated_at": tag.updated_at,
            "tenant_id": str(tag.tenant_id) if tag.tenant_id else None,
            "is_active": tag.is_active,
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật tag thành công",
            data=tag_response
        )

    except IntegrityError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.BAD_REQUEST,
            message="Tag với tên này đã tồn tại",
            data=str(e)
        )
    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def soft_delete_tag(tag_id: UUID, db: AsyncSession, current_user: User):
    """
    Xóa mềm tag (set is_active = 0)
    """
    try:
        # Check quyền super admin
        max_level_user = await isCheckMaxLevel(current_user, db)

        # Query tag
        query = select(Tag).where(Tag.id == tag_id)
        
        # Nếu không phải super admin, chỉ delete được tag của tenant mình
        if not max_level_user:
            query = query.where(Tag.tenant_id == current_user.tenant_id)

        tag = await db.scalar(query)

        if not tag:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Tag không tồn tại hoặc bạn không có quyền xóa"
            )

        # Soft delete
        tag.is_active = 0
        tag.updated_at = datetime.now(timezone.utc)

        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa tag thành công"
        )

    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def hard_delete_tag(tag_id: UUID, db: AsyncSession, current_user: User):
    """
    Xóa vĩnh viễn tag khỏi database (chỉ dành cho super admin)
    """
    try:
        # Chỉ super admin mới được hard delete
        max_level_user = await isCheckMaxLevel(current_user, db)
        
        if not max_level_user:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Bạn không có quyền xóa vĩnh viễn tag"
            )

        # Query tag
        tag = await db.scalar(select(Tag).where(Tag.id == tag_id))

        if not tag:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Tag không tồn tại"
            )

        # Hard delete
        await db.delete(tag)
        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa vĩnh viễn tag thành công"
        )

    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e)
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi không mong muốn",
            data=str(e)
        )


async def get_tag_statistics(db: AsyncSession, current_user: User):
    """
    Lấy thống kê tổng quan về tags.
    Trả về: total_tags, active_tags, inactive_tags, top_used_tags (top 10 theo ticket).
    Chỉ thống kê tags thuộc tenant của user (trừ super admin).
    """
    try:
        max_level_user = await isCheckMaxLevel(current_user, db)

        base_query = select(Tag)
        if not max_level_user:
            base_query = base_query.where(Tag.tenant_id == current_user.tenant_id)

        total_tags = await db.scalar(
            select(func.count()).select_from(base_query.subquery())
        ) or 0

        active_query = base_query.where(Tag.is_active == 1)
        active_tags = await db.scalar(
            select(func.count()).select_from(active_query.subquery())
        ) or 0

        inactive_tags = total_tags - active_tags

        tag_usage_query = (
            select(
                Tag.id,
                Tag.name,
                Tag.color,
                func.count(ticket_tag_association.c.ticket_id).label("usage_count"),
            )
            .select_from(Tag)
            .outerjoin(ticket_tag_association, Tag.id == ticket_tag_association.c.tag_id)
            .group_by(Tag.id, Tag.name, Tag.color)
            .order_by(func.count(ticket_tag_association.c.ticket_id).desc())
            .limit(10)
        )
        if not max_level_user:
            tag_usage_query = tag_usage_query.where(Tag.tenant_id == current_user.tenant_id)

        top_tags_result = await db.execute(tag_usage_query)
        top_tags = [
            {
                "id": str(row.id),
                "name": row.name,
                "color": row.color,
                "usage_count": row.usage_count,
            }
            for row in top_tags_result
        ]

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thống kê tags thành công",
            data={
                "total_tags": total_tags,
                "active_tags": active_tags,
                "inactive_tags": inactive_tags,
                "top_used_tags": top_tags,
            },
        )
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Đã xảy ra lỗi khi lấy thống kê",
            data=str(e),
        )
