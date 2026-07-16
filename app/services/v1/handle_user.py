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
from typing import Any, Optional, cast
from app.schemas.requests.user import CreateUserRequest, UpdateUserRequest
from app.utils.helpers import isCheckMaxLevel
from uuid import UUID
from datetime import datetime, timezone
from app.integrations.chatwoot import client as chatwoot_client
from app.db.models import ChatwootLegacyMap, ChatwootMapResourceType


def _prospective_user_meta_for_sync(user: User, update_data: dict[str, Any]) -> dict[str, Any] | None:
    if "meta_data" not in update_data:
        return user.meta_data if isinstance(user.meta_data, dict) else None
    inc = update_data["meta_data"]
    if not isinstance(inc, dict):
        return user.meta_data if isinstance(user.meta_data, dict) else None
    base = dict(user.meta_data) if isinstance(user.meta_data, dict) else {}
    if isinstance(inc.get("chatwoot_agent"), dict) and isinstance(
        base.get("chatwoot_agent"), dict
    ):
        return {
            **base,
            **inc,
            "chatwoot_agent": {
                **base["chatwoot_agent"],
                **inc["chatwoot_agent"],
            },
        }
    return {**base, **inc}


def _merge_chatwoot_agent_payload(
    *,
    meta_data: dict[str, Any] | None,
    core: dict[str, Any],
) -> dict[str, Any]:
    """Gộp meta_data thành payload Agent Chatwoot (phẳng, không gửi key `chatwoot_agent`).

    Thứ tự: flatten `meta_data.chatwoot_agent` trước, sau đó các key **root** của meta_data
    (trừ `chatwoot_agent`) ghi đè — để `meta_data.role` ở root không bị snapshot cũ trong
    `chatwoot_agent` đè mất (bug trước đây).
    """
    extras: dict[str, Any] = {}
    if isinstance(meta_data, dict):
        flat_nested: dict[str, Any] = {}
        nested = meta_data.get("chatwoot_agent")
        if isinstance(nested, dict):
            for k, v in nested.items():
                if v is not None:
                    flat_nested[k] = v
        root_flat: dict[str, Any] = {}
        for k, v in meta_data.items():
            if k == "chatwoot_agent":
                continue
            if v is not None:
                root_flat[k] = v
        extras = {**flat_nested, **root_flat}
    merged = {**core, **extras}
    out = {k: v for k, v in merged.items() if v is not None}
    out.pop("chatwoot_agent", None)
    return out


def _meta_data_triggers_chatwoot_agent_sync(meta: Any) -> bool:
    """True nếu client gửi meta_data có nội dung dùng để đồng bộ Agent Chatwoot (không chỉ object rỗng)."""
    if not isinstance(meta, dict) or not meta:
        return False
    nested = meta.get("chatwoot_agent")
    if isinstance(nested, dict) and len(nested) > 0:
        return True
    for k, v in meta.items():
        if k == "chatwoot_agent":
            continue
        if v is not None:
            return True
    return False


def _ensure_agent_payload_for_chatwoot(merged: dict[str, Any], user: User) -> None:
    """PATCH/POST agent thường cần name/email; bổ sung từ user nếu chỉ cập nhật meta_data."""
    if not merged.get("name"):
        merged["name"] = user.fullname or user.username
    if not merged.get("email"):
        merged["email"] = user.email


async def _get_chatwoot_account_id_for_tenant(
    db: AsyncSession, tenant_id: UUID
) -> int | None:
    stmt = select(ChatwootLegacyMap).where(
        and_(
            ChatwootLegacyMap.resource_type == ChatwootMapResourceType.ACCOUNT,
            ChatwootLegacyMap.local_uuid == tenant_id,
        )
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if not row:
        return None
    return row.chatwoot_id


async def _get_chatwoot_user_map_by_local(
    db: AsyncSession, local_user_id: UUID
) -> ChatwootLegacyMap | None:
    stmt = select(ChatwootLegacyMap).where(
        and_(
            ChatwootLegacyMap.resource_type == ChatwootMapResourceType.USER,
            ChatwootLegacyMap.local_uuid == local_user_id,
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


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

        tenant = await db.get(Tenant, user.tenant_id)

        user_data = {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "fullname": user.fullname,
            "is_active": user.is_active,
            "role": user.role.name if user.role else None,
            "level": user.level.name if user.level else None,
            "tenant_id": user.tenant_id,
            "graph_id": tenant.graph_id if tenant else None,
            "agent_id": tenant.agent_id if tenant else None,
            "graph_activated": tenant.graph_activated if tenant else None,
            "meta_data": user.meta_data,
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

        # Lấy danh sách ID người dùng để query hàng loạt permissions
        user_ids = [user.id for user in users]
        permissions_map = {}
        if user_ids:
            try:
                stmt_perm = (
                    select(User.id, Permission.name)
                    .join(Role, User.role_id == Role.id)
                    .join(RolePermission, Role.id == RolePermission.role_id)
                    .join(Permission, RolePermission.permission_id == Permission.id)
                    .where(User.id.in_(user_ids))
                )
                result_perm = await db.execute(stmt_perm)
                for uid, perm_name in result_perm.all():
                    if uid not in permissions_map:
                        permissions_map[uid] = []
                    permissions_map[uid].append(perm_name)
            except SQLAlchemyError as e:
                print(f"Error querying batch user permissions: {str(e)}")

        # Tạo danh sách dữ liệu trả về
        user_data = []
        for user in users:
            permissions = permissions_map.get(user.id, [])
            user_data.append({
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "fullname": user.fullname,
                "role": user.role.name if user.role else None,
                "level": user.level.name if user.level else None,
                "tenant_id": user.tenant_id,
                "is_active": user.is_active,
                "meta_data": user.meta_data,
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
                "meta_data": user.meta_data,
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

        if user_tenant_id is None:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message="Người dùng phải thuộc một tenant để đồng bộ Agent Chatwoot",
            )
        
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
            tenant_id=user_tenant_id,
            meta_data=dict(user_data.meta_data) if user_data.meta_data is not None else None,
        )

        db.add(new_user)
        await db.flush()

        # Tạo agent trực tiếp trong account Chatwoot của tenant để tránh quy trình tách rời.
        account_id = await _get_chatwoot_account_id_for_tenant(db, user_tenant_id)
        if account_id is None:
            await db.rollback()
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message="Tenant chưa được map với Chatwoot account, không thể tạo Agent",
            )

        chatwoot_core = {
            "name": new_user.fullname or new_user.username,
            "email": new_user.email,
            "role": "agent",
        }
        chatwoot_payload = _merge_chatwoot_agent_payload(
            meta_data=new_user.meta_data if isinstance(new_user.meta_data, dict) else None,
            core=chatwoot_core,
        )
        _ensure_agent_payload_for_chatwoot(chatwoot_payload, new_user)
        chatwoot_res = await chatwoot_client.application_request(
            "POST",
            f"/api/v1/accounts/{account_id}/agents",
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
                message="Tạo agent trên Chatwoot thất bại, đã rollback tạo user nội bộ",
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
                message="Chatwoot trả id agent không hợp lệ, đã rollback tạo user nội bộ",
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
        if not isinstance(new_user.meta_data, dict):
            new_user.meta_data = {}
        else:
            new_user.meta_data = dict(new_user.meta_data)
        new_user.meta_data["chatwoot_agent"] = {
            k: v for k, v in chatwoot_payload.items() if k != "password"
        }

        try:
            await db.commit()
        except SQLAlchemyError:
            await db.rollback()
            if chatwoot_created_id is not None:
                # Best-effort compensation để tránh orphan agent trên Chatwoot.
                await chatwoot_client.application_request(
                    "DELETE",
                    f"/api/v1/accounts/{account_id}/agents/{chatwoot_created_id}",
                )
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
                message="Lỗi commit CSDL, đã rollback và hủy agent Chatwoot",
            )

        await db.refresh(new_user)

        # Tạo response data không bao gồm password
        user_response = {
            "id": new_user.id,
            "username": new_user.username,
            "email": new_user.email,
            "fullname": new_user.fullname,
            "role_id": new_user.role_id,
            "level_id": new_user.level_id,
            "is_active": new_user.is_active,
            "tenant_id": new_user.tenant_id,
            "meta_data": new_user.meta_data,
            "chatwoot_synced": True,
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

        user_chatwoot_map = await _get_chatwoot_user_map_by_local(db, user.id)
        if (
            user_chatwoot_map is not None
            and user_data.tenant_id is not None
            and user_data.tenant_id != user.tenant_id
        ):
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message="Không hỗ trợ đổi tenant cho user đã đồng bộ Chatwoot. Hãy tạo user mới trong tenant đích",
            )
        
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
        # Lưu ý: nếu client gửi field = null, mặc định ta **không** coi đó là yêu cầu "clear"
        # để tránh vô tình xóa role_id/level_id/... khi frontend gửi null.
        update_data = user_data.model_dump(exclude_unset=True, exclude_none=True)
        
        # Flag to check if password is being changed
        password_changed = False
        
        raw_password_for_chatwoot = None

        # Handle password update separately
        if 'password' in update_data and update_data['password']:
            raw_password_for_chatwoot = update_data['password']
            # Hash the new password
            update_data['password'] = hash_password(update_data['password'])
            password_changed = True

        chatwoot_payload = {}
        if "fullname" in update_data and update_data["fullname"] is not None:
            chatwoot_payload["name"] = update_data["fullname"]
            chatwoot_payload["display_name"] = update_data["fullname"]
        if "email" in update_data and update_data["email"] is not None:
            chatwoot_payload["email"] = update_data["email"]
        if raw_password_for_chatwoot:
            chatwoot_payload["password"] = raw_password_for_chatwoot

        md_source_agent = _prospective_user_meta_for_sync(user, update_data)
        scalar_cw = bool(chatwoot_payload)
        # Toàn bộ meta_data dùng cho Chatwoot Agent: bất kỳ key nào (role, password, chatwoot_agent, …) đều kích hoạt sync.
        meta_chatwoot_sync = "meta_data" in update_data and _meta_data_triggers_chatwoot_agent_sync(
            update_data.get("meta_data")
        )
        requested_deactivate = "is_active" in update_data and update_data.get("is_active") == 0
        sync_agent = (scalar_cw or meta_chatwoot_sync) and not requested_deactivate
        chatwoot_merged: dict[str, Any] | None = None

        # Nếu disable user bằng update API thì bắt buộc xóa agent trên Chatwoot trước.
        if requested_deactivate:
            if user_chatwoot_map is not None:
                if user.tenant_id is not None:
                    account_id = await _get_chatwoot_account_id_for_tenant(db, user.tenant_id)
                    if account_id is not None:
                        cw_del_res = await chatwoot_client.application_request(
                            "DELETE",
                            f"/api/v1/accounts/{account_id}/agents/{user_chatwoot_map.chatwoot_id}",
                        )
                        if cw_del_res.status_code not in (200, 204, 404):
                            return api_response(
                                status=ResponseStatus.ERROR,
                                status_code=cw_del_res.status_code
                                if cw_del_res.status_code in (401, 403, 404, 422, 503)
                                else 502,
                                message="Không thể vô hiệu hóa vì xóa agent trên Chatwoot thất bại",
                                data={
                                    "chatwoot_status_code": cw_del_res.status_code,
                                    "chatwoot_response": cw_del_res.data,
                                },
                            )
                await db.delete(user_chatwoot_map)
                user.chat_id = None

        if sync_agent:
            account_id = await _get_chatwoot_account_id_for_tenant(db, user.tenant_id)
            if account_id is None:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=ResponseStatusCode.BAD_REQUEST,
                    message="Tenant chưa được map với Chatwoot account, không thể cập nhật Agent",
                )
            core_cw: dict[str, Any] = {"role": "agent"}
            core_cw.update(chatwoot_payload)
            chatwoot_merged = _merge_chatwoot_agent_payload(
                meta_data=md_source_agent if isinstance(md_source_agent, dict) else None,
                core=core_cw,
            )
            _ensure_agent_payload_for_chatwoot(chatwoot_merged, user)
            if user_chatwoot_map is None:
                create_payload = {
                    k: v for k, v in chatwoot_merged.items()
                }
                create_payload["name"] = (
                    create_payload.get("name") or user.fullname or user.username
                )
                create_payload["email"] = create_payload.get("email") or user.email
                # Giữ role theo payload đã merge từ meta_data (nếu có).
                # Nếu không có role thì mặc định vẫn là agent.
                create_payload.setdefault("role", "agent")
                if not create_payload.get("email"):
                    return api_response(
                        status=ResponseStatus.ERROR,
                        status_code=ResponseStatusCode.BAD_REQUEST,
                        message="User không có email để tạo Agent Chatwoot",
                    )
                create_res = await chatwoot_client.application_request(
                    "POST",
                    f"/api/v1/accounts/{account_id}/agents",
                    json_body=create_payload,
                )
                if (
                    create_res.status_code not in (200, 201)
                    or not isinstance(create_res.data, dict)
                    or create_res.data.get("id") is None
                ):
                    return api_response(
                        status=ResponseStatus.ERROR,
                        status_code=create_res.status_code
                        if create_res.status_code in (401, 404, 409, 422, 503)
                        else 502,
                        message="Tạo Agent trên Chatwoot thất bại",
                        data={
                            "chatwoot_status_code": create_res.status_code,
                            "chatwoot_response": create_res.data,
                        },
                    )
                try:
                    new_agent_id = int(create_res.data["id"])
                except (TypeError, ValueError):
                    return api_response(
                        status=ResponseStatus.ERROR,
                        status_code=502,
                        message="Chatwoot trả id agent không hợp lệ",
                        data={"chatwoot_response": create_res.data},
                    )
                user_chatwoot_map = ChatwootLegacyMap(
                    resource_type=ChatwootMapResourceType.USER,
                    local_uuid=user.id,
                    chatwoot_id=new_agent_id,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(user_chatwoot_map)
                user.chat_id = new_agent_id
                chatwoot_merged = create_payload
            else:
                cw_res = await chatwoot_client.application_request(
                    "PATCH",
                    f"/api/v1/accounts/{account_id}/agents/{user_chatwoot_map.chatwoot_id}",
                    json_body=chatwoot_merged,
                )
                if cw_res.status_code == 404:
                    create_payload = {
                        k: v for k, v in chatwoot_merged.items()
                    }
                    create_payload["name"] = (
                        create_payload.get("name") or user.fullname or user.username
                    )
                    create_payload["email"] = create_payload.get("email") or user.email
                    # Giữ role theo payload đã merge từ meta_data (nếu có).
                    # Nếu không có role thì mặc định vẫn là agent.
                    create_payload.setdefault("role", "agent")
                    if not create_payload.get("email"):
                        return api_response(
                            status=ResponseStatus.ERROR,
                            status_code=ResponseStatusCode.BAD_REQUEST,
                            message="User không có email để tạo lại Agent Chatwoot",
                        )
                    create_res = await chatwoot_client.application_request(
                        "POST",
                        f"/api/v1/accounts/{account_id}/agents",
                        json_body=create_payload,
                    )
                    if (
                        create_res.status_code not in (200, 201)
                        or not isinstance(create_res.data, dict)
                        or create_res.data.get("id") is None
                    ):
                        return api_response(
                            status=ResponseStatus.ERROR,
                            status_code=create_res.status_code
                            if create_res.status_code in (401, 404, 409, 422, 503)
                            else 502,
                            message="Agent Chatwoot không tồn tại và tái tạo thất bại",
                            data={
                                "chatwoot_status_code": create_res.status_code,
                                "chatwoot_response": create_res.data,
                            },
                        )
                    try:
                        new_agent_id = int(create_res.data["id"])
                    except (TypeError, ValueError):
                        return api_response(
                            status=ResponseStatus.ERROR,
                            status_code=502,
                            message="Chatwoot trả id agent không hợp lệ",
                            data={"chatwoot_response": create_res.data},
                        )
                    user_chatwoot_map.chatwoot_id = new_agent_id
                    user.chat_id = new_agent_id
                    chatwoot_merged = create_payload
                elif cw_res.status_code != 200:
                    return api_response(
                        status=ResponseStatus.ERROR,
                        status_code=cw_res.status_code
                        if cw_res.status_code in (401, 404, 409, 422, 503)
                        else 502,
                        message="Cập nhật agent trên Chatwoot thất bại",
                        data={
                            "chatwoot_status_code": cw_res.status_code,
                            "chatwoot_response": cw_res.data,
                        },
                    )
        
        for key, value in update_data.items():
            setattr(user, key, value)

        if chatwoot_merged is not None:
            md_u = dict(user.meta_data) if isinstance(user.meta_data, dict) else {}
            md_u["chatwoot_agent"] = {
                k: v for k, v in chatwoot_merged.items() if k != "password"
            }
            user.meta_data = md_u

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
            "role_id": user.role_id,
            "level_id": user.level_id,
            "is_active": user.is_active,
            "tenant_id": user.tenant_id,
            "meta_data": user.meta_data,
            "chatwoot_synced": user_chatwoot_map is not None,
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
     
        user_chatwoot_map = await _get_chatwoot_user_map_by_local(db, user.id)
        if user_chatwoot_map is not None:
            if user.tenant_id is not None:
                account_id = await _get_chatwoot_account_id_for_tenant(db, user.tenant_id)
                if account_id is not None:
                    cw_res = await chatwoot_client.application_request(
                        "DELETE",
                        f"/api/v1/accounts/{account_id}/agents/{user_chatwoot_map.chatwoot_id}",
                    )
                    if cw_res.status_code not in (200, 204, 404):
                        return api_response(
                            status=ResponseStatus.ERROR,
                            status_code=cw_res.status_code
                            if cw_res.status_code in (401, 403, 404, 422, 503)
                            else 502,
                            message="Xóa agent trên Chatwoot thất bại",
                            data={
                                "chatwoot_status_code": cw_res.status_code,
                                "chatwoot_response": cw_res.data,
                            },
                        )
            await db.delete(user_chatwoot_map)
            user.chat_id = None

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


async def sync_user_to_chatwoot_agent(user_id: UUID, db: AsyncSession, current_user: User):
    try:
        is_super_admin = await isCheckMaxLevel(current_user, db)
        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.NOT_FOUND,
                message="Người dùng không tồn tại",
            )

        if not is_super_admin and user.tenant_id != current_user.tenant_id:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.FORBIDDEN,
                message="Bạn chỉ có thể đồng bộ người dùng trong tenant của mình",
            )

        if user.tenant_id is None:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message="User chưa thuộc tenant, không thể đồng bộ Agent Chatwoot",
            )

        account_id = await _get_chatwoot_account_id_for_tenant(db, user.tenant_id)
        if account_id is None:
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.BAD_REQUEST,
                message="Tenant chưa được map với Chatwoot account",
            )

        user_map = await _get_chatwoot_user_map_by_local(db, user.id)
        agent_id: int | None = user_map.chatwoot_id if user_map else None
        created_agent_id: int | None = None
        create_core = {
            "name": user.fullname or user.username,
            "email": user.email,
            "role": "agent",
        }
        create_payload = _merge_chatwoot_agent_payload(
            meta_data=user.meta_data if isinstance(user.meta_data, dict) else None,
            core=create_core,
        )
        _ensure_agent_payload_for_chatwoot(create_payload, user)

        if agent_id is None:
            create_res = await chatwoot_client.application_request(
                "POST",
                f"/api/v1/accounts/{account_id}/agents",
                json_body=create_payload,
            )
            if (
                create_res.status_code not in (200, 201)
                or not isinstance(create_res.data, dict)
                or create_res.data.get("id") is None
            ):
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=create_res.status_code
                    if create_res.status_code in (401, 404, 409, 422, 503)
                    else 502,
                    message="Tạo agent trên Chatwoot thất bại",
                    data={"chatwoot_response": create_res.data},
                )
            try:
                agent_id = int(create_res.data["id"])
            except (TypeError, ValueError):
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=502,
                    message="Chatwoot trả id agent không hợp lệ",
                    data={"chatwoot_response": create_res.data},
                )
            created_agent_id = agent_id
        else:
            patch_res = await chatwoot_client.application_request(
                "PATCH",
                f"/api/v1/accounts/{account_id}/agents/{agent_id}",
                json_body=create_payload,
            )
            if patch_res.status_code == 404:
                recreate_res = await chatwoot_client.application_request(
                    "POST",
                    f"/api/v1/accounts/{account_id}/agents",
                    json_body=create_payload,
                )
                if (
                    recreate_res.status_code not in (200, 201)
                    or not isinstance(recreate_res.data, dict)
                    or recreate_res.data.get("id") is None
                ):
                    return api_response(
                        status=ResponseStatus.ERROR,
                        status_code=recreate_res.status_code
                        if recreate_res.status_code in (401, 404, 409, 422, 503)
                        else 502,
                        message="Không thể tái tạo agent trên Chatwoot",
                        data={"chatwoot_response": recreate_res.data},
                    )
                try:
                    agent_id = int(recreate_res.data["id"])
                except (TypeError, ValueError):
                    return api_response(
                        status=ResponseStatus.ERROR,
                        status_code=502,
                        message="Chatwoot trả id agent không hợp lệ",
                        data={"chatwoot_response": recreate_res.data},
                    )
                created_agent_id = agent_id
            elif patch_res.status_code != 200:
                return api_response(
                    status=ResponseStatus.ERROR,
                    status_code=patch_res.status_code
                    if patch_res.status_code in (401, 404, 409, 422, 503)
                    else 502,
                    message="Không thể cập nhật agent Chatwoot hiện có",
                    data={"chatwoot_response": patch_res.data},
                )

        if user_map is None:
            user_map = ChatwootLegacyMap(
                resource_type=ChatwootMapResourceType.USER,
                local_uuid=user.id,
                chatwoot_id=agent_id,
                created_at=datetime.now(timezone.utc),
            )
            db.add(user_map)
        else:
            user_map.chatwoot_id = agent_id

        user.chat_id = agent_id
        if not isinstance(user.meta_data, dict):
            user.meta_data = {}
        else:
            user.meta_data = dict(user.meta_data)
        user.meta_data["chatwoot_agent"] = {
            k: v for k, v in create_payload.items() if k != "password"
        }
        try:
            await db.commit()
        except SQLAlchemyError:
            await db.rollback()
            if created_agent_id is not None:
                await chatwoot_client.application_request(
                    "DELETE",
                    f"/api/v1/accounts/{account_id}/agents/{created_agent_id}",
                )
            return api_response(
                status=ResponseStatus.ERROR,
                status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR,
                message="Lỗi commit CSDL, đã rollback và hủy agent Chatwoot",
            )
        await db.refresh(user)

        return api_response(
            status=ResponseStatus.SUCCESS,
            status_code=ResponseStatusCode.OK,
            message="Đồng bộ user nội bộ thành Chatwoot Agent thành công",
            data={
                "user_id": user.id,
                "tenant_id": user.tenant_id,
                "meta_data": user.meta_data,
                "chatwoot_synced": True,
            },
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
