# services/user_service.py
from fastapi import Request, HTTPException, Depends
from app.core.config.database import get_db
from jose import jwt, JWTError
from app.core.config.app_config import settings
from app.db.models import User, Role, RolePermission, Permission, Levels, Group, GroupUser, Department, Tenant
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException, status
from app.core.security.jwt import get_user_id_from_token
from app.core.security.permissions import get_user_permissions
from sqlalchemy.orm import joinedload
from sqlalchemy import or_, func, asc, desc, and_
from app.core.security.password_utils import hash_password 
from sqlalchemy import update
from typing import Optional, cast
from app.schemas.requests.user import CreateUserRequest, UpdateUserRequest
from app.utils.helpers import isCheckMaxLevel
from uuid import UUID
from datetime import datetime, timezone
from app.integrations.chatwoot import client as chatwoot_client
from app.db.models import ChatwootLegacyMap, ChatwootMapResourceType


# Hàm tăng token_version để vô hiệu hóa tất cả token cũ
async def increment_token_version(user_id: UUID, db: AsyncSession):
    """
    Tăng token_version của user để vô hiệu hóa tất cả token cũ
    """
    user = await db.get(User, user_id)
    if user:
        user.token_version = (user.token_version if hasattr(user, 'token_version') else 0) + 1
        await db.commit()
        await db.refresh(user)

# Lấy thông tin người dùng từ token, dùng để check permission
async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = request.headers.get("Authorization")
    if not token or not token.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token không hợp lệ")

    try:
        payload = jwt.decode(token[7:], settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = payload.get("user_id")
        token_version = payload.get("token_version")  # Get token_version from token
        
        user_query = await db.execute(select(User).where(User.id == user_id))
        user = user_query.scalars().first()
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Người dùng không tồn tại")
        
        # Check token_version - if token version doesn't match, token is invalid
        user_token_version = user.token_version if hasattr(user, 'token_version') else 0
        if token_version is None or token_version != user_token_version:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token đã bị vô hiệu hóa")
        
        return user
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token không hợp lệ")
    except SQLAlchemyError:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Lỗi truy vấn DB")

# lấy thông tin người dùng, và lấy quyền khi đăng nhập vào hệ thống
async def get_current_user_or_none(request, db : AsyncSession):
    try:
        token = request.headers.get("Authorization")
        if not token or not token.startswith("Bearer "):
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.UNAUTHORIZED,
                message="Token không hợp lệ",
            )
        user_id = get_user_id_from_token(token)
        
        # Load user với role và level
        stmt = (
            select(User)
            .options(
                joinedload(User.role),
                joinedload(User.level)
            )
            .where(User.id == user_id)
        )
        result = await db.execute(stmt)
        user = result.scalars().first()
        
        if user is None:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.UNAUTHORIZED,
                message="Người dùng không tồn tại",
            )
        
        # Lấy danh sách quyền của người dùng
        permissions = await get_user_permissions(user_id, db)
        # Tạo response với thông tin người dùng và quyền
        user_data = {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "fullname": user.fullname,
            "is_active": user.is_active,
            "role": user.role.name if user.role else None,
            "level": user.level.name if user.level else None,
            "tenant_id": user.tenant_id,
            "permissions": permissions
        }
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin người dùng thành công",
            data=user_data
        )
    except SQLAlchemyError as e:
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu",
        )
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định",
        )
    
async def get_all_users(
    db: AsyncSession,
    current_user: User,
    id: UUID | None = None,
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc"
):
    try:
        if id:
            return await get_user_by_id(id, db, current_user)

        # Lấy max level_order
        stmt = select(func.max(Levels.level_order)).select_from(Levels)
        result = await db.execute(stmt)
        max_level_order = cast(int, result.scalar_one_or_none() or 0)

        # Lấy current user's level_order
        current_level_order = 0
        if current_user.level_id is not None:
            stmt = select(Levels.level_order).where(Levels.id == current_user.level_id)
            result = await db.execute(stmt)
            current_level_order = cast(int, result.scalar_one_or_none() or 0)

        # Tạo query cơ bản
        query = select(User).options(
            joinedload(User.role),
            joinedload(User.level)
        )

        # Thiết lập điều kiện lọc
        filters = [User.id != current_user.id]
        count_filters = [User.id != current_user.id]

        # Nếu không phải super admin
        if current_level_order != max_level_order:
            filters.extend([
                User.tenant_id == current_user.tenant_id,
                User.is_active == 1,
                Levels.level_order < current_level_order
            ])
            count_filters.extend(filters[1:])  

            query = query.join(Levels, User.level_id == Levels.id)
        
        query = query.where(*filters)

        # Thêm điều kiện tìm kiếm nếu có
        if search:
            search_expr = or_(
                User.username.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%"),
                User.fullname.ilike(f"%{search}%")
            )
            query = query.where(search_expr)
            count_filters.append(search_expr)

        # Sắp xếp
        if sort_by:
            sort_column = getattr(User, sort_by, None)
            if sort_column is not None:
                if sort_order.lower() == "desc":
                    query = query.order_by(sort_column.desc())
                else:
                    query = query.order_by(sort_column.asc())

        # Phân trang
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)

        # Thực thi truy vấn
        result = await db.execute(query)
        users = result.scalars().all()

        # Đếm tổng số
        count_query = select(func.count()).select_from(User)
        if current_level_order != max_level_order:
            count_query = count_query.join(Levels, User.level_id == Levels.id)
        count_query = count_query.where(*count_filters)

        total_count = await db.scalar(count_query) or 0
        total_pages = (total_count + page_size - 1) // page_size

        # Tạo danh sách dữ liệu trả về
        user_data = []
        for user in users:
            permissions = await get_user_permissions(user.id, db)
            user_data.append({
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "fullname": user.fullname,
                "role": user.role.name if user.role else None,
                "level": user.level.name if user.level else None,
                "tenant_id": user.tenant_id,
                "is_active": user.is_active,
                "permissions": permissions
            })

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy danh sách người dùng thành công",
            data={
                "items": user_data,
                "pagination": {
                    "total": total_count,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                }
            }
        )

    except SQLAlchemyError as e:
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu",
        )
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )


async def get_user_by_id(user_id: UUID, db: AsyncSession, current_user: User):
    try:
        # Get max level order
        stmt = select(func.max(Levels.level_order)).select_from(Levels)
        result = await db.execute(stmt)
        max_level_order = cast(int, result.scalar_one_or_none() or 0)

        # Get current user's level
        current_level_order = 0
        if current_user.level_id is not None:
            stmt = select(Levels).where(Levels.id == current_user.level_id)
            result = await db.execute(stmt)
            current_level = cast(Optional[Levels], result.scalar_one_or_none())
            if current_level is not None:
                current_level_order = cast(int, current_level.level_order)

        # Get target user with level check
        stmt = (
            select(User)
            .options(
                joinedload(User.role),
                joinedload(User.level)
            )
            .where(User.id == user_id)
        )
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if user is None:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Người dùng không tồn tại",
            )

        # Check if current user can access this user's info
        if current_level_order < max_level_order:  # Nếu không phải super admin
            if user.is_active == 0:
                return api_response(
                    status=ResponseStatus.WARNING,
                    status_code=ResponseStatusCode.NOT_FOUND,
                    message="Tài khoản không tồn tại, hoặc đã bị khóa",
                )
            if user.level_id is None or user.level.level_order >= current_level_order or user.tenant_id != current_user.tenant_id:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.FORBIDDEN,
                    message="Bạn không có quyền xem thông tin người dùng này",
                )

        permissions = await get_user_permissions(user_id, db)
        user_data = ({
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "fullname": user.fullname,
                "role": user.role.name if user.role else None,
                "level": user.level.name if user.level else None,
                "role_id": user.role.id if user.role else None,
                "level_id": user.level.id if user.level else None,
                "order_level": user.level.level_order if user.level else None,
                "tenant_id": user.tenant_id,
                "is_active": user.is_active,
                "permissions": permissions
            })
        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Lấy thông tin người dùng thành công",
            data=user_data
        )
    except SQLAlchemyError as e:
        print(f"Database error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu",
        )
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )

async def create_user(user_data : CreateUserRequest, db: AsyncSession, current_user: User):
    try:
        is_supper_admin = await isCheckMaxLevel(current_user, db)
        # check tenant_id
        if user_data.tenant_id:
            stmt = select(Tenant).where(and_(Tenant.id == user_data.tenant_id, Tenant.is_active == 1))
            result = await db.execute(stmt)
            if result.scalar_one_or_none() is None:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.BAD_REQUEST,
                    message="Tenant không tồn tại, hoặc không hợp lệ. Hãy kiểm tra lại"
                )
                
        # Xác định tenant_id cho user mới
        if not is_supper_admin:
            # User thường chỉ có thể tạo user trong tenant của mình
            user_tenant_id = current_user.tenant_id
            if user_data.tenant_id and user_data.tenant_id != current_user.tenant_id:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.FORBIDDEN,
                    message="Bạn chỉ có thể tạo người dùng trong tenant của mình"
                )
        else:
            # Super admin có thể chỉ định tenant
            user_tenant_id = user_data.tenant_id or current_user.tenant_id
        
        # Check username exists trong cùng tenant (không cho phép trùng username trong cùng tenant)
        stmt = select(User).where(
            and_(
                User.username == user_data.username,
                User.tenant_id == user_tenant_id
            )
        )
        result = await db.execute(stmt)
        if result.scalar_one_or_none():
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message=f"Tài khoản '{user_data.username}' đã tồn tại trong tenant này"
            )

        # Check email exists
        if user_data.email:
            stmt = select(User).where(and_(User.email == user_data.email))
            result = await db.execute(stmt)
            if result.scalar_one_or_none():
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.BAD_REQUEST,
                    message="Email đã tồn tại"
                )

        # Kiểm tra role_id nếu có
        if user_data.role_id:
            stmt = select(Role).where(Role.id == user_data.role_id, Role.is_active == 1)
            stmt_result = await db.scalar(stmt)
            if not stmt_result:
                return api_response(ResponseStatus.ERROR, ResponseStatusCode.NOT_FOUND, "Vai trò không tồn tại hoặc đã bị khóa")
            else:
                if not is_supper_admin and current_user.role.role_order <= stmt_result.role_order:
                    return api_response(ResponseStatus.ERROR, ResponseStatusCode.FORBIDDEN, "Bạn chỉ có thể tạo người dùng có vai trò nhỏ hơn vai trò của bạn")
            
        # Check level
        if user_data.level_id is not None:
            stmt = select(Levels).where(Levels.id == user_data.level_id)
            result = await db.execute(stmt)
            new_level = cast(Optional[Levels], result.scalar_one_or_none())
            if not new_level:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.NOT_FOUND,
                    message="Level không tồn tại"
                )
            # Check if current user can create user with this level
            if not is_supper_admin and new_level.level_order >= current_user.level.level_order:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.FORBIDDEN,
                    message="Bạn chỉ có thể tạo người dùng có level nhỏ hơn level của bạn"
                )
        
        # Create new user
        new_user = User(
            username=user_data.username,
            email=user_data.email,
            password=hash_password(user_data.password),
            fullname=user_data.fullname,
            chat_id=user_data.chat_id,
            role_id=user_data.role_id,
            level_id=user_data.level_id,
            tenant_id=user_tenant_id
        )

        db.add(new_user)
        await db.flush()

        # Tạo user trên Chatwoot trước khi commit local để đảm bảo all-or-nothing.
        chatwoot_payload = {
            "name": new_user.fullname or new_user.username,
            "display_name": new_user.fullname or new_user.username,
            "email": new_user.email,
            "password": user_data.password,
        }
        chatwoot_res = await chatwoot_client.platform_request(
            "POST",
            "/platform/api/v1/users",
            json_body=chatwoot_payload,
        )

        chatwoot_created_id: int | None = None
        if (
            chatwoot_res.status_code not in (200, 201)
            or not isinstance(chatwoot_res.data, dict)
            or chatwoot_res.data.get("id") is None
        ):
            await db.rollback()
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=chatwoot_res.status_code
                if chatwoot_res.status_code in (401, 404, 409, 422, 503)
                else 502,
                message="Tạo user trên Chatwoot thất bại, đã rollback tạo user nội bộ",
                data={
                    "chatwoot_status_code": chatwoot_res.status_code,
                    "chatwoot_response": chatwoot_res.data,
                },
            )
        try:
            chatwoot_created_id = int(chatwoot_res.data["id"])
        except (TypeError, ValueError):
            await db.rollback()
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=502,
                message="Chatwoot trả id user không hợp lệ, đã rollback tạo user nội bộ",
                data={"chatwoot_response": chatwoot_res.data},
            )

        db.add(
            ChatwootLegacyMap(
                resource_type=ChatwootMapResourceType.USER,
                local_uuid=new_user.id,
                chatwoot_id=chatwoot_created_id,
                created_at=datetime.now(timezone.utc),
            )
        )
        new_user.chat_id = chatwoot_created_id

        try:
            await db.commit()
        except SQLAlchemyError:
            await db.rollback()
            if chatwoot_created_id is not None:
                # Best-effort compensation để tránh orphan user trên Chatwoot.
                await chatwoot_client.platform_request(
                    "DELETE",
                    f"/platform/api/v1/users/{chatwoot_created_id}",
                )
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
                message="Lỗi commit CSDL, đã rollback và hủy user Chatwoot",
            )

        await db.refresh(new_user)

        # Tạo response data không bao gồm password
        user_response = {
            "id": new_user.id,
            "username": new_user.username,
            "email": new_user.email,
            "fullname": new_user.fullname,
            "chat_id": new_user.chat_id,
            "role_id": new_user.role_id,
            "level_id": new_user.level_id,
            "is_active": new_user.is_active,
            "tenant_id": new_user.tenant_id,
            "chatwoot_user_id": chatwoot_created_id,
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.CREATED,
            message="Tạo người dùng thành công",
            data=user_response
        )

    except SQLAlchemyError as e:
        print(e)
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi khi truy vấn cơ sở dữ liệu"
        )
    except Exception as e:
        print(f"error: {e}")
        return api_response(
            status=ResponseStatus.ERROR,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
            message="Lỗi không xác định"
        )

async def update_user(user_id: UUID, user_data : UpdateUserRequest, db: AsyncSession, current_user: User):
    try:
        is_supper_admin = await isCheckMaxLevel(current_user, db)
        stmt = None
        if is_supper_admin:
            # Get user to update
            stmt = select(User).where(User.id == user_id)
        else:
            stmt = select(User).where(and_(User.id == user_id, User.is_active == 1, User.tenant_id == current_user.tenant_id))
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Người dùng không tồn tại"
                )
        

        # Check if current user can update this user's level
        if not is_supper_admin and user.level.level_order >= current_user.level.level_order:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Không thể cập nhật người dùng có level cao hơn hoặc bằng level của bạn"
            )
        if not is_supper_admin and user.role.role_order >= current_user.role.role_order:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Không thể cập nhật người dùng có role cao hơn hoặc bằng role của bạn"
            )
            
        # check tenant_id
        if user_data.tenant_id:
            stmt = select(Tenant).where(Tenant.id == user_data.tenant_id)
            result = await db.execute(stmt)
            if result.scalar_one_or_none() is None:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.BAD_REQUEST,
                    message="Tenant không tồn tại, hoặc không hợp lệ. Hãy kiểm tra lại"
                )
                
        # Kiểm tra role_id nếu có
        if user_data.role_id:
            stmt = select(Role).where(Role.id == user_data.role_id, Role.is_active == 1)
            stmt_result = await db.scalar(stmt)
            if not stmt_result:
                return api_response(ResponseStatus.ERROR, ResponseStatusCode.NOT_FOUND, "Vai trò không tồn tại hoặc đã bị khóa")
            else:
                if not is_supper_admin and current_user.role.role_order <= stmt_result.role_order:
                    return api_response(ResponseStatus.ERROR, ResponseStatusCode.FORBIDDEN, "Bạn chỉ có cập nhật người dùng có vai trò nhỏ hơn vai trò của bạn")
            
        # Check if new level is valid
        if user_data.level_id is not None:
            stmt = select(Levels).where(Levels.id == user_data.level_id)
            result = await db.execute(stmt)
            new_level = cast(Optional[Levels], result.scalar_one_or_none())
            if not new_level:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.NOT_FOUND,
                    message="Level không tồn tại"
                )
            
        # Xác định tenant_id cho user sau khi update
        update_tenant_id = user_data.tenant_id if user_data.tenant_id else user.tenant_id
        
        # Check username unique trong cùng tenant khi update (nếu đổi username)
        if user_data.username and user_data.username != user.username:
            query_check_username = select(User).where(
                and_(
                    User.username == user_data.username,
                    User.tenant_id == update_tenant_id,
                    User.id != user_id
                )
            )
            existing_user = await db.scalar(query_check_username)
            if existing_user:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.CONFLICT,
                    message=f"Tài khoản '{user_data.username}' đã tồn tại trong tenant này"
                )
            
        # Update user data
        update_data = user_data.model_dump(exclude_unset=True)
        
        # Flag to check if password is being changed
        password_changed = False
        
        # Handle password update separately
        if 'password' in update_data and update_data['password']:
            # Hash the new password
            update_data['password'] = hash_password(update_data['password'])
            password_changed = True
        
        for key, value in update_data.items():
            setattr(user, key, value)

        if password_changed:
            user.token_version += 1

        await db.commit()
        await db.refresh(user)
        
        # Send notification if password was changed
        if password_changed:
            try:
                from app.services.v1.handle_notification import notification_service
                await notification_service.notify_password_changed(user.id)
            except Exception as e:
                print(f"Failed to send password change notification: {str(e)}")

        # Create response data
        user_response = {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "fullname": user.fullname,
            "chat_id": user.chat_id,
            "role_id": user.role_id,
            "level_id": user.level_id,
            "is_active": user.is_active
        }

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Cập nhật thông tin người dùng thành công",
            data=user_response
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

async def soft_delete_user(user_id: UUID, db: AsyncSession, current_user: User):
    try:
        stmt = None
        is_super_admin = await isCheckMaxLevel(current_user, db)
         # Kiểm tra user tồn tại
        if is_super_admin:
            stmt = select(User).where(User.id == user_id)
        else:
            stmt = select(User).where(and_(User.id == user_id, User.is_active == 1))
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Người dùng không tồn tại"
            )
        
        if not is_super_admin and  user.tenant_id != current_user.tenant_id:
            return  api_response(
                        status=ResponseStatus.ERROR,
                        status_code=ResponseStatusCode.FORBIDDEN,
                        message="Bạn chỉ có thể xóa người dùng trong tenant của mình"
                    )
        # Chỉ cho phép xóa nếu là admin hoặc target user có level thấp hơn
        if not is_super_admin  and user.level.level_order >= current_user.level.level_order:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Không thể xóa người dùng có level cao hơn hoặc bằng level của bạn"
            )
        
        if not is_super_admin and user.role.role_order >= current_user.role.role_order:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Không thể xóa người dùng có role cao hơn hoặc bằng role của bạn"
            )

        if user.is_active == 0:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message="Người dùng đã bị xóa từ trước rồi"
            )
     
            # Thực hiện xóa mềm bằng cách set is_active = 0 và tăng token_version để vô hiệu hóa token
        user.token_version = (user.token_version if hasattr(user, 'token_version') else 0) + 1
        user.is_active = 0
        await db.commit()
        await db.refresh(user)
        
        # Send notification and disconnect user
        try:
            from app.services.v1.handle_notification import notification_service
            await notification_service.notify_user_kicked(
                user_id=user.id,
                reason="Tài khoản của bạn đã bị vô hiệu hóa bởi quản trị viên"
            )
        except Exception as e:
            print(f"Failed to send kick notification: {str(e)}")

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Xóa người dùng thành công"
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

# async def get_user_groups(user_id, page, page_size, search, sort_by, sort_order, db, current_user):
#     try:
#         # Kiểm tra user tồn tại
#         stmt = select(User).where(User.id == user_id)
#         result = await db.execute(stmt)
#         user = result.scalar_one_or_none()
#         if not user:
#             return api_response(
#                 status=ResponseStatus.ERROR,
#                 status_code=ResponseStatusCode.NOT_FOUND,
#                 message="Người dùng không tồn tại"
#             )

#         # Xây dựng truy vấn chính
#         stmt = (
#             select(Group)
#             .join(GroupUser, Group.id == GroupUser.group_id)
#             .join(Department, Group.department_id == Department.id)
#             .where(GroupUser.user_id == user_id)
#         )

#         # Lọc theo từ khóa (name, description)
#         if search:
#             stmt = stmt.where(
#                 Group.name.ilike(f"%{search}%") | Group.description.ilike(f"%{search}%")
#             )

#         # Sắp xếp
#         if sort_by in ["name", "description"]:
#             sort_column = getattr(Group, sort_by, None)
#             if sort_column is not None:
#                 stmt = stmt.order_by(asc(sort_column) if sort_order == "asc" else desc(sort_column))
#         else:
#             stmt = stmt.order_by(desc(Group.id))  # mặc định

#         # Lấy tổng số bản ghi
#         count_stmt = (
#             select(func.count(Group.id))
#             .join(GroupUser, Group.id == GroupUser.group_id)
#             .where(GroupUser.user_id == user_id)
#         )
#         if search:
#             count_stmt = count_stmt.join(Department, Group.department_id == Department.id).where(
#                 Group.name.ilike(f"%{search}%") | Group.description.ilike(f"%{search}%")
#             )
#         result = await db.execute(count_stmt)
#         total = result.scalar_one_or_none() or 0

#         # Phân trang
#         stmt = stmt.offset((page - 1) * page_size).limit(page_size)
#         result = await db.execute(stmt)
#         groups = result.scalars().all()

#         # Chuẩn hóa dữ liệu phản hồi
#         data = []
#         for group in groups:
#             # Load department liên kết với group
#             stmt_dep = select(Department).where(Department.id == group.department_id)
#             result = await db.execute(stmt_dep)
#             department = result.scalar_one_or_none()
#             data.append({
#                 "id": group.id,
#                 "name": group.name,
#                 "description": group.description,
#                 "department": {
#                     "name": department.name if department else None,
#                     "description": department.description if department else None
#                 }
#             })

#         return api_response(
#             status=ResponseStatus.SUCCESS,
#             status_code=ResponseStatusCode.OK,
#             message="Lấy danh sách nhóm của người dùng thành công",
#             data={
#                 "items": data,
#                 "total": total,
#                 "page": page,
#                 "page_size": page_size
#             }
#         )

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
