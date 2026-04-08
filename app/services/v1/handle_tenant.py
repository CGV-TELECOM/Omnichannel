from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import Tenant, User
from app.schemas.requests.tenant import TenantCreate, TenantResponse, TenantUpdate 
from sqlalchemy import select, func,  or_
from uuid import UUID  
from sqlalchemy.future import select
from fastapi import Request
from app.utils.helpers import isCheckMaxLevel
from sqlalchemy.exc import SQLAlchemyError


async def getAllTenant(_: Request, current_user: User, id: UUID | None, page: int, page_size: int, search: str | None, db: AsyncSession):
    try:
        if not (await isCheckMaxLevel(current_user, db)):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới có thể truy cập tài nguyên này"
        )

        if id:
            query_tenant_raw = select(Tenant).where(Tenant.id == id)
            query_tenant_execute = await db.execute(query_tenant_raw)
            result_tenant = query_tenant_execute.scalar_one_or_none()

            if result_tenant is None:
                return api_response(ResponseStatus.INFO, ResponseStatusCode.BAD_REQUEST, "Tenant không tồn tại")

            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Tìm tenant theo ID thành công",
                TenantResponse(
                    id=result_tenant.id,
                    name=result_tenant.name,
                    description=result_tenant.description,
                    is_active=result_tenant.is_active
                )
            )
        else:
            query = select(Tenant)
            if search:
                search_text = f"%{search}%"
                query = query.where(
                    or_(
                        Tenant.name.ilike(search_text),
                        Tenant.description.ilike(search_text)
                    )
                )

            total_query = select(func.count()).select_from(query.subquery())
            total_result = await db.execute(total_query)
            total = total_result.scalar() or 0
            total_pages = (total + page_size - 1) // page_size

            offset = (page - 1) * page_size
            query = query.offset(offset).limit(page_size)
            result = await db.execute(query)
            tenants = result.scalars().all()

            tenant_list = [
                TenantResponse(
                    id=t.id,
                    name=t.name,
                    description=t.description,
                    is_active=t.is_active
                )
                for t in tenants
            ]

            return api_response(
                ResponseStatus.SUCCESS,
                ResponseStatusCode.OK,
                "Lấy danh sách tenant thành công",
                {
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                    "items": tenant_list
                }
            )

    except SQLAlchemyError as e:
        print(f"[DB ERROR] getAllTenant: {e}")
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Có lỗi xảy ra khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"[UNEXPECTED ERROR] getAllTenant: {e}")
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Có lỗi không xác định xảy ra"
        )

async def createTenant(_, current_user: User, tenant_data: TenantCreate, db: AsyncSession):
    try:
        if not (await isCheckMaxLevel(current_user, db)):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới có thể truy cập tài nguyên này"
            )
        # Kiểm tra tên tenant đã tồn tại (không phân biệt hoa thường)
        query_tenant = select(Tenant).where(
            func.upper(Tenant.name) == tenant_data.name.upper()
        )
        tenant_execute = await db.execute(query_tenant)
        tenant_result = tenant_execute.scalar_one_or_none()

        if tenant_result:
            return api_response(
                ResponseStatus.ERROR, 
                ResponseStatusCode.CONFLICT, 
                "Đã tồn tại tên tenant này rồi, vui lòng kiểm tra lại"
            )  

        # Tạo tenant mới
        new_tenant = Tenant(
            name=tenant_data.name,
            description=tenant_data.description
        )
        db.add(new_tenant)
        await db.commit()
        await db.refresh(new_tenant)

        return api_response(
            ResponseStatus.SUCCESS, 
            ResponseStatusCode.CREATED, 
            "Thêm tenant thành công",
            data=new_tenant
        )

    except Exception as e:
        await db.rollback()
        # Ghi log nếu cần
        print(f"[ERROR] createTenant: {e}")
        return api_response(
            ResponseStatus.ERROR,
            ResponseStatusCode.INTERNAL_SERVER_ERROR,
            "Đã có lỗi xảy ra khi thêm tenant, vui lòng thử lại sau."
        )

async def updateTenant(
    tenant_id: UUID,
    current_user: User,
    _: Request,  
    tenant_data: TenantUpdate,
    db: AsyncSession
):
    try:
        # 1. Kiểm tra quyền
        if not (await isCheckMaxLevel(current_user, db)):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới có thể truy cập tài nguyên này"
            )
        
        # 2. Kiểm tra trùng tên tenant (trừ chính tenant đang cập nhật)
        tenant_query = await db.execute(
            select(Tenant).where(
                func.upper(Tenant.name) == tenant_data.name.upper(),
                Tenant.id != tenant_id
            )
        )
        existing_tenant = tenant_query.scalar_one_or_none()
        if existing_tenant:
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.CONFLICT,
                "Đã tồn tại tên tenant trong hệ thống"
            )
        
        # 3. Tìm tenant theo ID
        target_query = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = target_query.scalar_one_or_none()
        if tenant is None:
            return api_response(
                ResponseStatus.INFO,
                ResponseStatusCode.BAD_REQUEST,
                "Không tìm thấy tenant, vui lòng kiểm tra lại"
            )
        
        # 4. Cập nhật dữ liệu
        for field, value in tenant_data.dict(exclude_unset=True).items():
            setattr(tenant, field, value)

        await db.commit()
        await db.refresh(tenant)

        return api_response(
            ResponseStatus.SUCCESS,
            ResponseStatusCode.OK,
            "Cập nhật tenant thành công",
            data=TenantResponse(
                id=tenant.id,
                name=tenant.name,
                description=tenant.description,
                is_active=tenant.is_active
            )
        )

    except SQLAlchemyError as e:
        await db.rollback()
        print(f"[DB ERROR] updateTenant: {e}")
        return api_response(
            ResponseStatus.ERROR, 
            ResponseStatusCode.INTERNAL_SERVER_ERROR, 
            "Có lỗi xảy ra khi thao tác với cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"[UNEXPECTED ERROR] updateTenant: {e}")
        return api_response(
            ResponseStatus.ERROR, 
            ResponseStatusCode.INTERNAL_SERVER_ERROR, 
            "Có lỗi không xác định xảy ra"
        )


async def deleteTenant(tenant_id: UUID, current_user: User, request, db: AsyncSession):
    try:
        if not (await isCheckMaxLevel(current_user, db)):
            return api_response(
                ResponseStatus.ERROR,
                ResponseStatusCode.FORBIDDEN,
                "Chỉ quản trị viên mới có thể truy cập tài nguyên này"
        )
        if tenant_id is None:
            return api_response(
                ResponseStatus.INFO, 
                ResponseStatusCode.BAD_REQUEST, 
                "Không tồn tại tenant_id. Vui lòng kiểm tra lại đầu vào"
            )

        query_tenant_raw = select(Tenant).where(Tenant.id == tenant_id)
        query_tenant_execute = await db.execute(query_tenant_raw) 
        result_tenant = query_tenant_execute.scalar_one_or_none()

        if result_tenant is None:
            return api_response(
                ResponseStatus.INFO, 
                ResponseStatusCode.BAD_REQUEST, 
                "Không tìm thấy tenant, vui lòng kiểm tra lại"
            )

        # Đánh dấu xóa mềm
        result_tenant.is_active = 0

        await db.commit()
        await db.refresh(result_tenant)

        return api_response(
            ResponseStatus.SUCCESS, 
            ResponseStatusCode.OK, 
            "Xóa tenant thành công"
        )
    except SQLAlchemyError as e:
        await db.rollback()
        print(f"[DB ERROR] deleteTenant: {e}")
        return api_response(
            ResponseStatus.ERROR, 
            ResponseStatusCode.INTERNAL_SERVER_ERROR, 
            "Có lỗi xảy ra khi thao tác với cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"[UNEXPECTED ERROR] deleteTenant: {e}")
        return api_response(
            ResponseStatus.ERROR, 
            ResponseStatusCode.INTERNAL_SERVER_ERROR, 
            "Có lỗi không xác định xảy ra"
        )
