from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode

from app.db.models import Permission, RolePermission
from sqlalchemy import select, update, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import or_
from sqlalchemy import func

from collections import defaultdict
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from app.utils.helpers import isCheckMaxLevel
from app.db.models import User, Tenant
from app.schemas.requests.permission import CreatePermissionRequest, CreatePermissionTenantRequest
from uuid import UUID


# async def get_permissions(
#     db: AsyncSession,
#     current_user: User,
#     search: str | None = None,
#     id: int | None = None
# ):
#     try:
#         if id:
#             return await get_permission_by_id(id, db, current_user)
#         else:
#             user_max_level = await isCheckMaxLevel(current_user, db)

#             query = select(Permission)

#             if not user_max_level:
#                 query = query.where(Permission.tenant_id == current_user.tenant_id)

#             # Thêm điều kiện tìm kiếm nếu có
#             if search:
#                 search = f"%{search}%"
#                 query = query.where(
#                     or_(
#                         Permission.name.ilike(search),
#                         Permission.description.ilike(search)
#                     )
#                 )

#             result = await db.execute(query)
#             permissions = result.scalars().all()

#             # Không cần nhóm nữa, trả về 1 mảng phẳng
#             flat_permissions = []
#             for permission in permissions:
#                 permission_name = str(permission.name)
#                 if any(permission_name.startswith(prefix) for prefix in ("view_", "create_", "edit_", "delete_")):
#                     permission_name = permission_name.split("_")[0]

#                 flat_permissions.append({
#                     "id": permission.id,
#                     "name": permission_name,
#                     "description": permission.description,
#                     "belong_to": permission.belong_to  # nếu bạn vẫn muốn giữ thông tin này
#                 })

#             return api_response(
#                 status=ResponseStatus.SUCCESS,
#                 status_code=ResponseStatusCode.OK,
#                 message="Lấy danh sách quyền thành công",
#                 data=flat_permissions
#             )


#     except SQLAlchemyError as e:
#         print(f"Database error: {str(e)}")
#         return api_response(
#             status=ResponseStatus.ERROR,
#             status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
#             message="Lỗi khi truy vấn cơ sở dữ liệu"
#         )
#     except Exception as e:
#         print(f"Unexpected error: {str(e)}")
#         return api_response(
#             status=ResponseStatus.ERROR,
#             status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
#             message="Lỗi không xác định"
#         )


async def get_permissions(
    db: AsyncSession,
    current_user: User,
    search: str | None = None,
    id: UUID | None = None
):
    try:
        if id:
            return await get_permission_by_id(id, db, current_user)

        user_max_level = await isCheckMaxLevel(current_user, db)

        query = select(Permission)

        if not user_max_level:
            query = query.where(Permission.tenant_id == current_user.tenant_id)

        if search:
            search = f"%{search}%"
            query = query.where(
                or_(
                    Permission.name.ilike(search),
                    Permission.description.ilike(search)
                )
            )

        result = await db.execute(query)
        permissions = result.scalars().all()
        count = len(permissions)

        # ===============================
        # GROUP THEO PREFIX (TRƯỚC "_")
        # ===============================
        grouped_permissions = defaultdict(list)

        for permission in permissions:
            permission_name = permission.name

            # luôn tách trước dấu "_"
            action = permission_name.split("_", 1)[0]

            grouped_permissions[action].append({
                "id": permission.id,
                "name": permission.name,
                "description": permission.description,
                "belong_to": permission.belong_to
            })

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách quyền thành công" + (f" (Tổng: {count} quyền)" if count > 0 else ""),
            data=grouped_permissions
        )

    except SQLAlchemyError as e:
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )


async def get_permission_by_id(permission_id: UUID, db: AsyncSession, current_user: User):
    try:
        user_max_level = await isCheckMaxLevel(current_user, db)

        if user_max_level:
            stmt = select(Permission).where(
                Permission.id == permission_id
            )
        else:
            # Chỉ được lấy quyền trong tenant của chính mình
            stmt = select(Permission).where(
                Permission.id == permission_id,
                Permission.tenant_id == current_user.tenant_id,
                Permission.is_active == 1
            )

        result = await db.execute(stmt)
        permission = result.scalar_one_or_none()

        if not permission:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Quyền không tồn tại"
            )

        permission_data = {
            "id": permission.id,
            "name": permission.name,
            "description": permission.description
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin quyền thành công",
            data=permission_data
        )

    except SQLAlchemyError as e:
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )


async def create_tenant_permission(
    request_data: CreatePermissionTenantRequest,
    db: AsyncSession,
    current_user: User
):
    try:
        user_level_max = await isCheckMaxLevel(current_user, db)

        if not user_level_max:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Tài khoản không có quyền tạo",
            )

        # Kiểm tra tenant tồn tại chưa
        check_stmt = select(Tenant).where(
            Tenant.id == request_data.tenant_id
        )
        tenant_result = await db.execute(check_stmt)
        tenant = tenant_result.scalar_one_or_none()

        if not tenant:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Tenant không tồn tại"
            )
 
        permissions = []

        for item in request_data.permissions:
            # Kiểm tra trùng lặp
            existing_stmt = select(Permission).where(
                Permission.name == item.name,
                Permission.tenant_id == request_data.tenant_id
            )
            existing = await db.execute(existing_stmt)
            if existing.scalar():
                continue

            new_permission = Permission(
                name=item.name,
                description=item.description,
                tenant_id=request_data.tenant_id
            )
            db.add(new_permission)
            permissions.append(new_permission)

        if not permissions:
            return api_response(
                status=ResponseStatus.WARNING,
                status_code=ResponseStatusCode.CONFLICT,
                message="Tất cả quyền đã tồn tại trong tenant"
            )

        await db.commit()

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message=f"Đã tạo {len(permissions)} quyền cho tenant"
        )

    except SQLAlchemyError as e:
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )

async def update_permission(permission_id: UUID, permission_data, db: AsyncSession, current_user: User):
    try:
        user_level_max = await isCheckMaxLevel(current_user, db)

        if not user_level_max:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Tài khoản không có quyền tạo",
            )

        # Kiểm tra permission tồn tại
        stmt = select(Permission).where(
            Permission.id == permission_id
        )
        result = await db.execute(stmt)
        permission = result.scalar_one_or_none()
        
        if not permission:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Quyền không tồn tại"
            )

        # Kiểm tra tên mới đã tồn tại chưa (nếu cập nhật tên)
        if permission_data.tenant_id is None: 
             return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Thiếu tenant"
            )
        
        if permission_data.name and permission_data.name != permission.name:
            stmt = select(Permission).where(
                Permission.name == permission_data.name,
                Permission.tenant_id == permission_data.tenant_id,
                Permission.id != permission.id 
            )
            result = await db.execute(stmt)
            if result.scalar_one_or_none():
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.CONFLICT,
                    message="Tên quyền đã tồn tại trong tenant"
                )

        # Cập nhật thông tin
        update_data = permission_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(permission, key, value)

        await db.commit()
        await db.refresh(permission)

        permission_response = {
            "id": permission.id,
            "name": permission.name,
            "description": permission.description
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật quyền thành công",
            data=permission_response
        )

    except SQLAlchemyError as e:
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )

async def delete_permission(permission_id: UUID, db: AsyncSession, current_user: User):
    try:
        user_level_max = await isCheckMaxLevel(current_user, db)
        if not user_level_max:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Tài khoản không có quyền xóa",
            )

        # Kiểm tra permission tồn tại
        stmt = select(Permission).where(
            Permission.id == permission_id
        )
        result = await db.execute(stmt)
        permission = result.scalar_one_or_none()
        
        if not permission:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Quyền không tồn tại"
            )
        
        # Kiểm tra xem quyền có được sử dụng trong role không
        stmt = select(RolePermission).where(
            RolePermission.permission_id == permission_id,
            RolePermission.tenant_id == permission.tenant_id
        )
        result = await db.execute(stmt)
        if result.scalar_one_or_none():
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.CONFLICT,
                message="Quyền đang được sử dụng trong role"
            )
        
        # Cập nhật permission thành inactive và xóa tất cả bản ghi ở role permission
        await db.execute(
            update(Permission)
            .where(Permission.id == permission_id)
            .values(is_active=0)  
        )
        # Xóa các role_permission liên quan
        await db.execute(
            delete(RolePermission).where(
                and_(
                    RolePermission.permission_id == permission_id,
                    RolePermission.tenant_id == permission.tenant_id
                )
            )
        )        
        await db.commit()   

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa quyền khỏi tenant thành công"
        )

    except SQLAlchemyError as e:
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )