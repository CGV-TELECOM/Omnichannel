from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, List, cast

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload
from uuid import UUID

from app.db.models import Customer, Tenant, User, Tag, TagType, Levels
from app.schemas.responses.api_response_rule import (
    api_response,
    ResponseStatus,
    ResponseStatusCode,
)
from app.schemas.requests.customer import (
    CustomerCreateRequest,
    CustomerUpdateRequest,
)
from app.schemas.requests.customer_tag import CustomerTagUpdateRequest
from app.utils.helpers import is_platform_admin


async def _get_tenant_if_required(
    db: AsyncSession, current_user: User
) -> Optional[Tenant]:
    """
    Đảm bảo tenant của current_user đang active (với user thường).
    Super admin thì không bị ràng buộc bởi tenant hiện tại.
    """

    is_super_admin = await is_platform_admin(current_user, db)
    if is_super_admin:
        return None

    if not current_user.tenant_id:
        return None

    tenant = await db.scalar(
        select(Tenant).where(
            Tenant.id == current_user.tenant_id,
            Tenant.is_active == 1,
        )
    )

    return tenant


async def _load_customer_with_tenant_guard(
    customer_id: UUID, db: AsyncSession, current_user: User
) -> Optional[Customer]:
    """
    Load 1 customer với rule:
    - Super admin: truy cập được mọi customer
    - User thường: chỉ truy cập được customer cùng tenant
    """

    is_super_admin = await is_platform_admin(current_user, db)
    query = select(Customer).options(selectinload(Customer.tags)).where(Customer.id == customer_id)

    if not is_super_admin:
        query = query.where(Customer.tenant_id == current_user.tenant_id)

    return await db.scalar(query)


async def _apply_tags_to_customer(
    *,
    db: AsyncSession,
    current_user: User,
    customer: Customer,
    tag_ids: Optional[List[UUID]],
) -> None:
    """
    Gán danh sách tag (type=CUSTOMER) cho customer.
    Nếu tag_ids = None -> không đụng tới quan hệ.
    Nếu tag_ids = [] -> clear hết tags.
    """

    if tag_ids is None:
        return

    if not tag_ids:
        customer.tags = []
        return

    is_super_admin = await is_platform_admin(current_user, db)

    tags_query = select(Tag).where(
        Tag.id.in_(tag_ids),
        Tag.type == TagType.CUSTOMER,
    )

    if not is_super_admin:
        tags_query = tags_query.where(Tag.tenant_id == current_user.tenant_id)

    result = await db.execute(tags_query)
    tags = result.scalars().all()

    if len(tags) != len(set(tag_ids)):
        # Có ít nhất 1 tag không hợp lệ/không thuộc quyền
        raise ValueError("Một hoặc nhiều tag không tồn tại hoặc bạn không có quyền sử dụng")

    customer.tags = list(tags)


async def get_customers(
    db: AsyncSession,
    current_user: User,
    id: UUID | None = None,
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc",
    is_active: int | None = None,
):
    """
    Lấy danh sách customer với phân trang, tìm kiếm, filter theo tenant.
    Nếu truyền id -> trả về chi tiết 1 customer.
    """
    try:
        if id:
            return await get_customer_by_id(id, db, current_user)

        is_super_admin = await is_platform_admin(current_user, db)

        # Tính level_order hiện tại để chặn view lên level bằng/ cao hơn
        current_level_order: int = 0
        if current_user.level_id is not None:
            level_stmt = select(Levels.level_order).where(Levels.id == current_user.level_id)
            level_res = await db.execute(level_stmt)
            current_level_order = level_res.scalar_one_or_none() or 0

        if not is_super_admin:
            tenant = await _get_tenant_if_required(db, current_user)
            if not tenant:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.FORBIDDEN,
                    message="Tenant không tồn tại hoặc đã bị vô hiệu hóa",
                )

        # Base query:
        # - Super admin: thấy tất cả
        # - User thường: chỉ thấy customer trong tenant mình và do user có level thấp hơn tạo
        if is_super_admin:
            base_query = select(Customer).options(selectinload(Customer.tags))
        else:
            base_query = (
                select(Customer)
                .options(selectinload(Customer.tags))
                .join(User, Customer.created_by == User.id)
                .join(Levels, User.level_id == Levels.id)
                .where(
                    Customer.tenant_id == current_user.tenant_id,
                    Levels.level_order < current_level_order,
                )
            )

        if is_active is not None:
            base_query = base_query.where(Customer.is_active == is_active)

        if search:
            like_search = f"%{search}%"
            base_query = base_query.where(
                or_(
                    Customer.name.ilike(like_search),
                    Customer.phone.ilike(like_search),
                    Customer.email.ilike(like_search),
                )
            )

        if sort_by and hasattr(Customer, sort_by):
            sort_col = getattr(Customer, sort_by)
            base_query = base_query.order_by(
                sort_col.desc() if sort_order.lower() == "desc" else sort_col.asc()
            )
        else:
            base_query = base_query.order_by(Customer.created_at.desc())

        count_query = select(func.count()).select_from(base_query.subquery())
        total_count = await db.scalar(count_query) or 0
        total_pages = (total_count + page_size - 1) // page_size

        base_query = base_query.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(base_query)
        customers = result.scalars().all()

        data = []
        for c in customers:
            tags_data = [
                {
                    "id": t.id,
                    "name": t.name,
                    "description": t.description,
                    "color": t.color,
                    "type": t.type.value if hasattr(t.type, "value") else str(t.type),
                }
                for t in (c.tags or [])
            ]
            data.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "phone": c.phone,
                    "email": c.email,
                    "tenant_id": c.tenant_id,
                    "created_by": c.created_by,
                    "created_at": c.created_at,
                    "updated_at": c.updated_at,
                    "meta_data": c.meta_data,
                    "is_active": c.is_active,
                    "tag_ids": [t.id for t in (c.tags or [])],
                    "tags": tags_data,
                }
            )

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách khách hàng thành công",
            data={
                "items": data,
                "pagination": {
                    "total": total_count,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                },
            },
        )
    except ValueError as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.BAD_REQUEST,
            message=str(e),
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


async def get_customer_by_id(customer_id: UUID, db: AsyncSession, current_user: User):
    """
    Lấy thông tin chi tiết 1 customer theo ID, theo rule tenant/super admin.
    """
    try:
        customer = await _load_customer_with_tenant_guard(
            customer_id=customer_id, db=db, current_user=current_user
        )
        if not customer:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Khách hàng không tồn tại hoặc bạn không có quyền truy cập",
            )

        # Không cho phép xem khách hàng được tạo bởi user có level >= mình (trừ super admin)
        is_super_admin = await is_platform_admin(current_user, db)
        if not is_super_admin and current_user.level_id is not None:
            level_stmt = select(Levels.level_order).where(Levels.id == current_user.level_id)
            level_res = await db.execute(level_stmt)
            current_level_order = level_res.scalar_one_or_none() or 0

            if customer.created_by is not None:
                creator_stmt = (
                    select(User, Levels.level_order)
                    .options(selectinload(User.level_id))
                    .join(Levels, User.level_id == Levels.id)
                    .where(User.id == customer.created_by)
                )
                creator_res = await db.execute(creator_stmt)
                creator_row = creator_res.first()
                if creator_row is not None:
                    _, creator_level_order = creator_row
                    if creator_level_order >= current_level_order:
                        return api_response(
                            status=ResponseStatus.ERROR,
                            status_code=ResponseStatusCode.FORBIDDEN,
                            message="Bạn không có quyền xem khách hàng này",
                        )

        data = {
            "id": customer.id,
            "name": customer.name,
            "phone": customer.phone,
            "email": customer.email,
            "tenant_id": customer.tenant_id,
            "created_by": customer.created_by,
            "created_at": customer.created_at,
            "updated_at": customer.updated_at,
            "meta_data": customer.meta_data,
            "is_active": customer.is_active,
            "tag_ids": [t.id for t in (customer.tags or [])],
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin khách hàng thành công",
            data=data,
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


async def get_customer_tags(customer_id: UUID, db: AsyncSession, current_user: User):
    """
    Lấy danh sách tag của một customer, theo rule tenant + level giống get_customer_by_id.
    """
    try:
        # Tái sử dụng rule view customer
        customer = await _load_customer_with_tenant_guard(
            customer_id=customer_id, db=db, current_user=current_user
        )
        if not customer:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Khách hàng không tồn tại hoặc bạn không có quyền truy cập",
            )

        is_super_admin = await is_platform_admin(current_user, db)
        if not is_super_admin and current_user.level_id is not None:
            level_stmt = select(Levels.level_order).where(Levels.id == current_user.level_id)
            level_res = await db.execute(level_stmt)
            current_level_order = level_res.scalar_one_or_none() or 0

            if customer.created_by is not None:
                creator_stmt = (
                    select(User, Levels.level_order)
                    .join(Levels, User.level_id == Levels.id)
                    .where(User.id == customer.created_by)
                )
                creator_res = await db.execute(creator_stmt)
                creator_row = creator_res.first()
                if creator_row is not None:
                    _, creator_level_order = creator_row
                    if creator_level_order >= current_level_order:
                        return api_response(
                            status=ResponseStatus.ERROR,
                            status_code=ResponseStatusCode.FORBIDDEN,
                            message="Bạn không có quyền xem khách hàng này",
                        )

        tags_data = [
            {
                "id": tag.id,
                "name": tag.name,
                "description": tag.description,
                "color": tag.color,
                "type": tag.type.value if hasattr(tag.type, "value") else str(tag.type),
            }
            for tag in (customer.tags or [])
        ]

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách tag của khách hàng thành công",
            data=tags_data,
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


async def create_customer(
    customer_data: CustomerCreateRequest,
    db: AsyncSession,
    current_user: User,
):
    """
    Tạo mới customer.
    - User thường: bắt buộc customer.tenant_id = current_user.tenant_id
    - Super admin: có thể chỉ định tenant_id hoặc để None.
    - created_by luôn là current_user.id (nếu có).
    - Hỗ trợ gán tags (type=CUSTOMER).
    """
    try:
        is_super_admin = await is_platform_admin(current_user, db)

        tenant_id: Optional[UUID]
        if is_super_admin:
            tenant_id = customer_data.tenant_id or current_user.tenant_id
        else:
            tenant_id = current_user.tenant_id
            if customer_data.tenant_id and customer_data.tenant_id != tenant_id:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.FORBIDDEN,
                    message="Bạn chỉ có thể tạo khách hàng trong tenant của mình",
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

        # Check trùng phone/email trong cùng tenant (nếu muốn chặt hơn)
        filters = [Customer.tenant_id == tenant_id] if tenant_id else []
        if customer_data.phone:
            phone_query = select(func.count()).select_from(Customer).where(
                *filters, Customer.phone == customer_data.phone
            )
            phone_exist = await db.scalar(phone_query) or 0
            if phone_exist:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.CONFLICT,
                    message="Số điện thoại đã tồn tại trong hệ thống",
                )

        if customer_data.email:
            email_query = select(func.count()).select_from(Customer).where(
                *filters, Customer.email == customer_data.email
            )
            email_exist = await db.scalar(email_query) or 0
            if email_exist:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.CONFLICT,
                    message="Email đã tồn tại trong hệ thống",
                )

        new_customer = Customer(
            name=customer_data.name,
            phone=customer_data.phone,
            email=customer_data.email,
            tenant_id=tenant_id,
            created_by=current_user.id if current_user.id else None,
            meta_data=customer_data.meta_data,
            is_active=1,
        )

        db.add(new_customer)

        if customer_data.tag_ids is not None:
            await _apply_tags_to_customer(
                db=db,
                current_user=current_user,
                customer=new_customer,
                tag_ids=customer_data.tag_ids,
            )

        await db.commit()
        await db.refresh(new_customer)

        customer_with_tags = await db.scalar(
            select(Customer)
            .options(selectinload(Customer.tags))
            .where(Customer.id == new_customer.id)
        )

        data = {
            "id": new_customer.id,
            "name": new_customer.name,
            "phone": new_customer.phone,
            "email": new_customer.email,
            "tenant_id": new_customer.tenant_id,
            "created_by": new_customer.created_by,
            "created_at": new_customer.created_at,
            "updated_at": new_customer.updated_at,
            "meta_data": new_customer.meta_data,
            "is_active": new_customer.is_active,
            "tag_ids": [t.id for t in (customer_with_tags.tags or [])],        
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message="Tạo khách hàng thành công",
            data=data,
        )
    except ValueError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.BAD_REQUEST,
            message=str(e),
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


async def update_customer(
    customer_id: UUID,
    customer_data: CustomerUpdateRequest,
    db: AsyncSession,
    current_user: User,
):
    """
    Cập nhật thông tin customer (multi-tenant + super-admin rule).
    """
    try:
        customer = await _load_customer_with_tenant_guard(
            customer_id=customer_id, db=db, current_user=current_user
        )
        if not customer:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Khách hàng không tồn tại hoặc bạn không có quyền cập nhật",
            )

        is_super_admin = await is_platform_admin(current_user, db)

        # Không cho phép sửa khách hàng được tạo bởi user có level >= mình (trừ super admin)
        if not is_super_admin and current_user.level_id is not None:
            level_stmt = select(Levels.level_order).where(Levels.id == current_user.level_id)
            level_res = await db.execute(level_stmt)
            current_level_order = level_res.scalar_one_or_none() or 0

            if customer.created_by is not None:
                creator_stmt = (
                    select(User, Levels.level_order)
                    .join(Levels, User.level_id == Levels.id)
                    .where(User.id == customer.created_by)
                )
                creator_res = await db.execute(creator_stmt)
                creator_row = creator_res.first()
                if creator_row is not None:
                    _, creator_level_order = creator_row
                    if creator_level_order >= current_level_order:
                        return api_response(
                            status=ResponseStatus.ERROR,
                            status_code=ResponseStatusCode.FORBIDDEN,
                            message="Bạn không có quyền cập nhật khách hàng này",
                        )

        # Xử lý đổi tenant (chỉ super admin)
        if customer_data.tenant_id is not None:
            if not is_super_admin:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.FORBIDDEN,
                    message="Bạn không có quyền thay đổi tenant của khách hàng",
                )

            if customer_data.tenant_id:
                tenant = await db.scalar(
                    select(Tenant).where(
                        Tenant.id == customer_data.tenant_id,
                        Tenant.is_active == 1,
                    )
                )
                if not tenant:
                    return api_response(
                        status=ResponseStatus.ERROR,
                        status_code=ResponseStatusCode.BAD_REQUEST,
                        message="Tenant không tồn tại hoặc đã bị vô hiệu hóa",
                    )
            customer.tenant_id = customer_data.tenant_id

        # Check trùng phone/email nếu có thay đổi
        filters = []
        if customer.tenant_id:
            filters.append(Customer.tenant_id == customer.tenant_id)
        filters.append(Customer.id != customer_id)

        if customer_data.phone and customer_data.phone != customer.phone:
            phone_query = (
                select(func.count())
                .select_from(Customer)
                .where(*filters, Customer.phone == customer_data.phone)
            )
            phone_exist = await db.scalar(phone_query) or 0
            if phone_exist:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.CONFLICT,
                    message="Số điện thoại đã tồn tại trong hệ thống",
                )

        if customer_data.email and customer_data.email != customer.email:
            email_query = (
                select(func.count())
                .select_from(Customer)
                .where(*filters, Customer.email == customer_data.email)
            )
            email_exist = await db.scalar(email_query) or 0
            if email_exist:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.CONFLICT,
                    message="Email đã tồn tại trong hệ thống",
                )

        update_data = customer_data.model_dump(exclude_unset=True)
        # tenant_id và is_active đã/ sẽ xử lý riêng, không cần setattr mù
        update_data.pop("tenant_id", None)

        tag_ids = cast(Optional[List[UUID]], update_data.pop("tag_ids", None))

        for key, value in update_data.items():
            if hasattr(customer, key):
                setattr(customer, key, value)

        if tag_ids is not None:
            await _apply_tags_to_customer(
                db=db,
                current_user=current_user,
                customer=customer,
                tag_ids=tag_ids,
            )

        customer.updated_at = datetime.now(timezone.utc)

        await db.commit()
        customer = await db.scalar(
            select(Customer)
            .options(selectinload(Customer.tags))
            .where(Customer.id == customer_id)
        )

        data = {
            "id": customer.id,
            "name": customer.name,
            "phone": customer.phone,
            "email": customer.email,
            "tenant_id": customer.tenant_id,
            "created_by": customer.created_by,
            "created_at": customer.created_at,
            "updated_at": customer.updated_at,
            "meta_data": customer.meta_data,
            "is_active": customer.is_active,
            "tag_ids": tag_ids if tag_ids is not None else [t.id for t in (customer.tags or [])]        
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật khách hàng thành công",
            data=data,
        )
    except ValueError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.BAD_REQUEST,
            message=str(e),
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


async def soft_delete_customer(
    customer_id: UUID,
    db: AsyncSession,
    current_user: User,
):
    """
    Xóa mềm customer (is_active = 0).
    Rule:
    - Chỉ super admin mới được phép xóa khách hàng.
    """
    try:
        # Chỉ cho phép super admin xóa customer, thống nhất với rule chặt chẽ của hệ thống
        is_super_admin = await is_platform_admin(current_user, db)
        if not is_super_admin:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Bạn không có quyền xóa khách hàng",
            )

        customer = await _load_customer_with_tenant_guard(
            customer_id=customer_id, db=db, current_user=current_user
        )
        if not customer:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Khách hàng không tồn tại",
            )

        # Không cho phép xóa khách hàng được tạo bởi user có level >= mình (trong trường hợp super admin đa cấp)
        if current_user.level_id is not None:
            level_stmt = select(Levels.level_order).where(Levels.id == current_user.level_id)
            level_res = await db.execute(level_stmt)
            current_level_order = level_res.scalar_one_or_none() or 0

            if customer.created_by is not None:
                creator_stmt = (
                    select(User, Levels.level_order)
                    .join(Levels, User.level_id == Levels.id)
                    .where(User.id == customer.created_by)
                )
                creator_res = await db.execute(creator_stmt)
                creator_row = creator_res.first()
                if creator_row is not None:
                    _, creator_level_order = creator_row
                    if creator_level_order > current_level_order:
                        return api_response(
                            status=ResponseStatus.ERROR,
                            status_code=ResponseStatusCode.FORBIDDEN,
                            message="Bạn không có quyền xóa khách hàng này",
                        )

        if customer.is_active == 0:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message="Khách hàng đã bị vô hiệu hóa trước đó",
            )

        customer.is_active = 0
        customer.updated_at = datetime.now(timezone.utc)

        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa khách hàng thành công",
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


async def add_tags_to_customer(
    customer_id: UUID,
    payload: CustomerTagUpdateRequest,
    db: AsyncSession,
    current_user: User,
):
    """
    Thêm (merge) danh sách tag vào customer.
    Không xóa các tag đang có, chỉ bổ sung thêm.
    Áp dụng rule tenant + level giống update_customer.
    """
    try:
        customer = await _load_customer_with_tenant_guard(
            customer_id=customer_id, db=db, current_user=current_user
        )
        if not customer:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Khách hàng không tồn tại hoặc bạn không có quyền cập nhật",
            )

        is_super_admin = await is_platform_admin(current_user, db)

        # Không cho phép chỉnh sửa khách hàng được tạo bởi user có level >= mình
        if not is_super_admin and current_user.level_id is not None:
            level_stmt = select(Levels.level_order).where(Levels.id == current_user.level_id)
            level_res = await db.execute(level_stmt)
            current_level_order = level_res.scalar_one_or_none() or 0

            if customer.created_by is not None:
                creator_stmt = (
                    select(User, Levels.level_order)
                    .join(Levels, User.level_id == Levels.id)
                    .where(User.id == customer.created_by)
                )
                creator_res = await db.execute(creator_stmt)
                creator_row = creator_res.first()
                if creator_row is not None:
                    _, creator_level_order = creator_row
                    if creator_level_order >= current_level_order:
                        return api_response(
                            status=ResponseStatus.ERROR,
                            status_code=ResponseStatusCode.FORBIDDEN,
                            message="Bạn không có quyền cập nhật khách hàng này",
                        )

        existing_ids = {t.id for t in (customer.tags or [])}
        new_ids = set(payload.tag_ids)
        merged_ids = list(existing_ids | new_ids)

        await _apply_tags_to_customer(
            db=db,
            current_user=current_user,
            customer=customer,
            tag_ids=merged_ids,
        )

        customer.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(customer)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Gán tag cho khách hàng thành công",
            data={
                "customer_id": customer.id,
                "tag_ids": [t.id for t in (customer.tags or [])],
            },
        )
    except ValueError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.BAD_REQUEST,
            message=str(e),
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


async def remove_tags_from_customer(
    customer_id: UUID,
    payload: CustomerTagUpdateRequest,
    db: AsyncSession,
    current_user: User,
):
    """
    Gỡ 1 hoặc nhiều tag ra khỏi customer.
    Áp dụng rule tenant + level giống update_customer.
    """
    try:
        customer = await _load_customer_with_tenant_guard(
            customer_id=customer_id, db=db, current_user=current_user
        )
        if not customer:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Khách hàng không tồn tại hoặc bạn không có quyền cập nhật",
            )

        is_super_admin = await is_platform_admin(current_user, db)

        # Không cho phép chỉnh sửa khách hàng được tạo bởi user có level >= mình
        if not is_super_admin and current_user.level_id is not None:
            level_stmt = select(Levels.level_order).where(Levels.id == current_user.level_id)
            level_res = await db.execute(level_stmt)
            current_level_order = level_res.scalar_one_or_none() or 0

            if customer.created_by is not None:
                creator_stmt = (
                    select(User, Levels.level_order)
                    .join(Levels, User.level_id == Levels.id)
                    .where(User.id == customer.created_by)
                )
                creator_res = await db.execute(creator_stmt)
                creator_row = creator_res.first()
                if creator_row is not None:
                    _, creator_level_order = creator_row
                    if creator_level_order >= current_level_order:
                        return api_response(
                            status=ResponseStatus.ERROR,
                            status_code=ResponseStatusCode.FORBIDDEN,
                            message="Bạn không có quyền cập nhật khách hàng này",
                        )

        remove_set = set(payload.tag_ids)
        remaining_ids = [t.id for t in (customer.tags or []) if t.id not in remove_set]

        await _apply_tags_to_customer(
            db=db,
            current_user=current_user,
            customer=customer,
            tag_ids=remaining_ids,
        )

        customer.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(customer)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Gỡ tag khỏi khách hàng thành công",
            data={
                "customer_id": customer.id,
                "tag_ids": [t.id for t in (customer.tags or [])],
            },
        )
    except ValueError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.BAD_REQUEST,
            message=str(e),
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

