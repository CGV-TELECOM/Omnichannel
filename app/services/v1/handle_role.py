from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response
from app.schemas.responses.api_response_rule import ResponseStatus, ResponseStatusCode
from app.db.models import Role, User
from sqlalchemy import select, func, update, and_
from app.schemas.requests.role import CreateRoleRequest, UpdateRoleRequest, RoleResponse
from app.utils.helpers import isCheckMaxLevel
from uuid import UUID

async def get_roles(
    id: UUID | None,
    page: int,
    page_size: int,
    search: str,
    sort_by: str,
    sort_order: str,
    db: AsyncSession,
    current_user: User
):
    try:
        is_super_admin = await isCheckMaxLevel(current_user, db)

        if id:
            return await get_role_by_id(id, current_user, is_super_admin, db)

        offset = (page - 1) * page_size
        filters = []
        if not is_super_admin:
            filters.append(Role.tenant_id == current_user.tenant_id)
            filters.append(Role.is_active == 1)
            filters.append(Role.role_order < current_user.role.role_order)
        if search:
            filters.append(Role.name.ilike(f"%{search}%"))

        query = select(Role).where(*filters)

        if sort_by and hasattr(Role, sort_by):
            column = getattr(Role, sort_by)
            query = query.order_by(column.desc() if sort_order == "desc" else column.asc())

        query = query.offset(offset).limit(page_size)
        result = await db.execute(query)
        roles = result.scalars().all()

        count_query = select(func.count()).select_from(Role).where(*filters)
        total_records = await db.scalar(count_query) or 0
        total_pages = (total_records + page_size - 1) // page_size

        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Lấy danh sách vai trò thành công",
            data={
                "roles": [RoleResponse.model_validate(level) for level in roles],  # Bạn nên serialize roles nếu cần
                "total_pages": total_pages,
                "total_records": total_records
            },
            status_code=ResponseStatusCode.OK
        )

    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(e),
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )


async def get_role_by_id(role_id: UUID, current_user: User, is_super_admin: bool, db: AsyncSession):
    try:
        filters = [Role.id == role_id]
        if not is_super_admin:
            filters.extend([
                Role.tenant_id == current_user.tenant_id,
                Role.is_active == 1,
                Role.role_order < current_user.role.role_order
            ])

        role = await db.scalar(select(Role).where(*filters))

        if not role:
            return api_response(
                status=ResponseStatus.INFO,
                message="Không tìm thấy vai trò này",
                status_code=ResponseStatusCode.BAD_REQUEST
            )
        role_data = RoleResponse.model_validate(role)
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Lấy vai trò thành công",
            data=role_data,  
            status_code=ResponseStatusCode.OK
        )

    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(e),
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )


async def create_role(role_data: CreateRoleRequest, current_user: User, db: AsyncSession):
    try:
        # Check quyền supper admin
        is_super_admin = await isCheckMaxLevel(current_user, db)

        if not is_super_admin and current_user.role.role_order <= role_data.role_order:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Bạn chỉ có thể tạo role nhỏ hơn role_order của mình",
                data=None,
                status_code=ResponseStatusCode.BAD_REQUEST
            )

        # Nếu không phải supper admin -> kiểm tra tenant_id
        if not is_super_admin and current_user.tenant_id != role_data.tenant_id:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Bạn chỉ có thể tạo role trong tenant của mình",
                data=None,
                status_code=ResponseStatusCode.BAD_REQUEST
            )

        # Kiểm tra role đã tồn tại chưa
        result = await db.execute(
        select(Role).where(
            and_(
                func.upper(Role.name) == role_data.name.upper(),
                Role.tenant_id == role_data.tenant_id
            )
        )
        )

        existing_role = result.scalar_one_or_none()

        if existing_role:
            if existing_role.is_active == 1:
                return api_response(
                    status=ResponseStatus.ERROR,
                    message="Vai trò đã tồn tại",
                    data=None,
                    status_code=ResponseStatusCode.BAD_REQUEST
                )
            else:
                # Khôi phục role đã bị vô hiệu hóa
                existing_role.description = role_data.description
                existing_role.role_order=role_data.role_order
                existing_role.is_active = 1
                db.add(existing_role)
                await db.commit()
                return api_response(
                    status=ResponseStatus.SUCCESS,
                    message="Tạo vai trò thành công",
                    data=None,
                    status_code=ResponseStatusCode.CREATED
                )

        # Tạo mới vai trò
        new_role = Role(
            name=role_data.name,
            description=role_data.description,
            tenant_id=role_data.tenant_id,
            role_order=role_data.role_order
        )
        db.add(new_role)
        await db.commit()
        await db.refresh(new_role)

        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Tạo vai trò thành công",
            data=new_role,
            status_code=ResponseStatusCode.CREATED
        )

    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(e),
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )

    
async def update_role(
    role_id: UUID,
    role_data: UpdateRoleRequest,
    current_user: User,
    db: AsyncSession
):
    try:
        # Kiểm tra quyền supper admin
        is_super_admin = await isCheckMaxLevel(current_user, db)
        # Nếu không phải supper admin => chỉ được phép sửa role trong tenant của mình
        if not is_super_admin and role_data.tenant_id != current_user.tenant_id:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Bạn không có quyền cập nhật vai trò ngoài tenant của mình",
                data=None,
                status_code=ResponseStatusCode.FORBIDDEN
            )
        if not is_super_admin and current_user.role.role_order <= role_data.role_order:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Bạn chỉ có thể cập nhật role nhỏ hơn role_order của mình",
                data=None,
                status_code=ResponseStatusCode.BAD_REQUEST
            )

        # Lấy vai trò cần cập nhật
        role = await db.scalar(
            select(Role).where(Role.id == role_id)
        )

        if not role:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Vai trò không tồn tại",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND
            )


        # Check trùng tên vai trò khác (cùng tenant nhưng khác id)
        existing_role = await db.scalar(
            select(Role).where(
                and_(
                    func.upper(Role.name) == role_data.name.upper(),
                    Role.tenant_id == role.tenant_id,
                    Role.id != role_id
                )
            )
        )
        if existing_role and existing_role.is_active == 1:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Tên vai trò đã tồn tại trong hệ thống",
                data=None,
                status_code=ResponseStatusCode.BAD_REQUEST
            )

        # Cập nhật thông tin vai trò 
        role.name = role_data.name
        role.description = role_data.description
        role.is_active = role_data.is_active
        role.tenant_id = role_data.tenant_id
        role.role_order = role_data.role_order

        await db.commit()
        await db.refresh(role)

        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Cập nhật vai trò thành công",
            data=role,
            status_code=ResponseStatusCode.OK
        )

    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(e),
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )


async def delete_role(role_id: UUID, current_user: User, db: AsyncSession):
    try:
        # Check quyền supper admin
        is_super_admin = await isCheckMaxLevel(current_user, db)
        role_query = None
        if not is_super_admin:
            # Lấy vai trò cần xóa
            role_query = await db.execute(
                select(Role).where(and_(Role.id == role_id, Role.is_active == 1, Role.tenant_id == current_user.tenant_id))
            )
        else:
            role_query = await db.execute(
                select(Role).where(and_(Role.id == role_id, Role.is_active == 1))
            )
        
        role = role_query.scalar_one_or_none()

        if not role:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Vai trò không tồn tại",
                data=None,
                status_code=ResponseStatusCode.NOT_FOUND
            )
        if not is_super_admin and role.role_order >= current_user.role.role_order:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Bạn chỉ có thể xóa role nhỏ hơn role_order của mình",
                data=None,
                status_code=ResponseStatusCode.BAD_REQUEST
            )

        # Kiểm tra xem có user nào đang dùng role không
        user_using_role = await db.scalar(
            select(User).where(User.role_id == role_id)
        )

        if user_using_role:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Không thể xóa vai trò đang có người dùng được sử dụng",
                data=None,
                status_code=ResponseStatusCode.BAD_REQUEST
            )

        role.is_active = 0
        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Xóa vai trò thành công",
            data=None,
            status_code=ResponseStatusCode.OK
        )

    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            message=str(e),
            data=None,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
