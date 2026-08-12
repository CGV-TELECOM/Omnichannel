from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import CustomerProvidedInfo, User, Tenant
from app.schemas.responses.api_response_rule import (
    api_response,
    ResponseStatus,
    ResponseStatusCode,
)
from app.schemas.requests.customer_provided_info import (
    CustomerProvidedInfoCreate,
    CustomerProvidedInfoUpdate,
    CustomerProvidedInfoResponse,
)
from app.utils.helpers import is_platform_admin


async def _get_tenant_guard(db: AsyncSession, current_user: User) -> bool:
    """Check if tenant is active (only for non-superadmins)"""
    is_super_admin = await is_platform_admin(current_user, db)
    if is_super_admin:
        return True

    if not current_user.tenant_id:
        return False

    tenant = await db.scalar(
        select(Tenant).where(
            Tenant.id == current_user.tenant_id,
            Tenant.is_active == 1,
        )
    )
    return tenant is not None


async def get_customer_provided_info(
    db: AsyncSession,
    current_user: User,
    id: Optional[UUID] = None,
    page: int = 1,
    page_size: int = 10,
    search: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "desc",
):
    """Lấy danh sách hoặc chi tiết thông tin KH cung cấp"""
    try:
        is_super_admin = await is_platform_admin(current_user, db)

        # Check tenant active
        if not is_super_admin:
            tenant_active = await _get_tenant_guard(db, current_user)
            if not tenant_active:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.FORBIDDEN,
                    message="Tenant không tồn tại hoặc đã bị vô hiệu hóa",
                )

        if id:
            query = select(CustomerProvidedInfo).where(CustomerProvidedInfo.id == id)
            if not is_super_admin:
                query = query.where(CustomerProvidedInfo.tenant_id == current_user.tenant_id)

            info = await db.scalar(query)
            if not info:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.NOT_FOUND,
                    message="Không tìm thấy thông tin được cung cấp bởi khách hàng",
                )

            return api_response(
                status=ResponseStatus.SUCCESS,
                status_code=ResponseStatusCode.OK,
                message="Lấy thông tin thành công",
                data=CustomerProvidedInfoResponse.model_validate(info),
            )

        # Query base
        base_query = select(CustomerProvidedInfo)
        if not is_super_admin:
            base_query = base_query.where(CustomerProvidedInfo.tenant_id == current_user.tenant_id)

        # Search
        if search:
            like_search = f"%{search}%"
            base_query = base_query.where(
                or_(
                    CustomerProvidedInfo.name.ilike(like_search),
                    CustomerProvidedInfo.email.ilike(like_search),
                    CustomerProvidedInfo.phone.ilike(like_search),
                    CustomerProvidedInfo.description.ilike(like_search),
                )
            )

        # Sort
        if sort_by and hasattr(CustomerProvidedInfo, sort_by):
            sort_col = getattr(CustomerProvidedInfo, sort_by)
            base_query = base_query.order_by(
                sort_col.desc() if sort_order.lower() == "desc" else sort_col.asc()
            )
        else:
            base_query = base_query.order_by(CustomerProvidedInfo.created_at.desc())

        # Pagination
        count_query = select(func.count()).select_from(base_query.subquery())
        total_count = await db.scalar(count_query) or 0
        total_pages = (total_count + page_size - 1) // page_size

        base_query = base_query.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(base_query)
        items = result.scalars().all()

        data_list = [CustomerProvidedInfoResponse.model_validate(item) for item in items]

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách thông tin KH cung cấp thành công",
            data={
                "items": data_list,
                "pagination": {
                    "total": total_count,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                },
            },
        )
    except SQLAlchemyError as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
        )
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định",
            data=str(e),
        )


async def create_customer_provided_info(
    info_data: CustomerProvidedInfoCreate,
    db: AsyncSession,
    current_user: User,
):
    """Tạo mới thông tin KH cung cấp"""
    try:
        is_super_admin = await is_platform_admin(current_user, db)

        tenant_id: Optional[UUID]
        if is_super_admin:
            tenant_id = info_data.tenant_id or current_user.tenant_id
        else:
            tenant_id = current_user.tenant_id
            if info_data.tenant_id and info_data.tenant_id != tenant_id:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.FORBIDDEN,
                    message="Bạn chỉ có thể tạo thông tin trong tenant của mình",
                )

        if tenant_id:
            tenant = await db.scalar(
                select(Tenant).where(
                    Tenant.id == tenant_id,
                    Tenant.is_active == 1,
                )
            )
            if not tenant:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.BAD_REQUEST,
                    message="Tenant không tồn tại hoặc đã bị vô hiệu hóa",
                )

        new_info = CustomerProvidedInfo(
            name=info_data.name,
            email=info_data.email,
            phone=info_data.phone,
            description=info_data.description,
            tenant_id=tenant_id,
        )

        db.add(new_info)
        await db.commit()
        await db.refresh(new_info)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message="Tạo thông tin thành công",
            data=CustomerProvidedInfoResponse.model_validate(new_info),
        )
    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định",
            data=str(e),
        )


async def update_customer_provided_info(
    info_id: UUID,
    info_data: CustomerProvidedInfoUpdate,
    db: AsyncSession,
    current_user: User,
):
    """Cập nhật thông tin KH cung cấp"""
    try:
        is_super_admin = await is_platform_admin(current_user, db)

        query = select(CustomerProvidedInfo).where(CustomerProvidedInfo.id == info_id)
        if not is_super_admin:
            query = query.where(CustomerProvidedInfo.tenant_id == current_user.tenant_id)

        info = await db.scalar(query)
        if not info:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Không tìm thấy thông tin được cung cấp bởi khách hàng hoặc bạn không có quyền cập nhật",
            )

        if info_data.tenant_id is not None:
            if not is_super_admin:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.FORBIDDEN,
                    message="Bạn không có quyền thay đổi tenant của thông tin này",
                )
            if info_data.tenant_id:
                tenant = await db.scalar(
                    select(Tenant).where(
                        Tenant.id == info_data.tenant_id,
                        Tenant.is_active == 1,
                    )
                )
                if not tenant:
                    return api_response(
                        status=ResponseStatus.ERROR,
                        status_code=ResponseStatusCode.BAD_REQUEST,
                        message="Tenant không tồn tại hoặc đã bị vô hiệu hóa",
                    )
            info.tenant_id = info_data.tenant_id

        update_data = info_data.model_dump(exclude_unset=True)
        update_data.pop("tenant_id", None)

        for key, value in update_data.items():
            if hasattr(info, key):
                setattr(info, key, value)

        info.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(info)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật thông tin thành công",
            data=CustomerProvidedInfoResponse.model_validate(info),
        )
    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định",
            data=str(e),
        )


async def delete_customer_provided_info(
    info_id: UUID,
    db: AsyncSession,
    current_user: User,
):
    """Xóa thông tin KH cung cấp"""
    try:
        is_super_admin = await is_platform_admin(current_user, db)

        query = select(CustomerProvidedInfo).where(CustomerProvidedInfo.id == info_id)
        if not is_super_admin:
            query = query.where(CustomerProvidedInfo.tenant_id == current_user.tenant_id)

        info = await db.scalar(query)
        if not info:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Không tìm thấy thông tin hoặc bạn không có quyền xóa",
            )

        await db.delete(info)
        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa thông tin thành công",
        )
    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
        )
    except Exception as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định",
            data=str(e),
        )
