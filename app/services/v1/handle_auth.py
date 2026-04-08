from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from app.schemas.responses.api_response_rule import api_response, ResponseStatus, ResponseStatusCode
from app.db.models import User, RefreshToken, Log, Tenant, Role
from sqlalchemy import  delete, and_
from sqlalchemy.future import select
from app.core.security.password_utils import hash_password, verify_password
from app.schemas.responses.api_response_rule import api_response
from app.core.security.jwt import create_access_token, create_refresh_token, verify_token, verify_refresh_token
from datetime import datetime, timezone
import json

async def login(form_data, request, db: AsyncSession):
    try:
        ip_address = request.client.host
        user_agent = request.headers.get('user-agent')

        # Tìm tenant theo name_tenant
        query_tenant = await db.execute(
            select(Tenant).where(
                and_(
                    Tenant.name == form_data.name_tenant,
                    Tenant.is_active == 1
                )
            )
        )
        tenant = query_tenant.scalar_one_or_none()
        
        if not tenant:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Tenant không tồn tại hoặc đã bị vô hiệu hóa",
                data=None,
                status_code=ResponseStatusCode.UNAUTHORIZED
            )

        # Tìm user với username và tenant_id
        query = select(User).where(
            and_(
                User.username == form_data.username,
                User.tenant_id == tenant.id,
                User.is_active == 1
            )
        )
        result = await db.execute(query)
        user = result.scalar_one_or_none()

        if not user or not verify_password(form_data.password, str(user.password)):
            return api_response(
                status=ResponseStatus.ERROR,
                message="Tên đăng nhập hoặc mật khẩu không đúng",
                data=None,
                status_code=ResponseStatusCode.UNAUTHORIZED
            )
        
        query_role = await db.execute(select(Role).where(Role.id == user.role_id, Role.is_active == 1))
        role_result = query_role.scalar_one_or_none()
        if not role_result:
                return api_response(
                status=ResponseStatus.ERROR,
                message="Người dùng chưa được gán vai trò nào, vui lòng liên hệ quản trị viên",
                data=None,
                status_code=ResponseStatusCode.UNAUTHORIZED
            )
        
        delete_query = delete(RefreshToken).where(RefreshToken.user_id == user.id)
        await db.execute(delete_query)  
        
        
        # Include token_version in token payload
        token_version = user.token_version if hasattr(user, 'token_version') else 0
        access_token = create_access_token(data={
            "user_id": str(user.id), 
            "tenant_id": str(user.tenant_id) if user.tenant_id else None,
            "token_version": token_version
        })
        refresh_token = create_refresh_token(data={
            "user_id": str(user.id), 
            "tenant_id": str(user.tenant_id) if user.tenant_id else None,
            "token_version": token_version
        })
    
        
        # save in refresh_token
        new_refresh_token = RefreshToken(
            refresh_token=refresh_token,
            ip=ip_address,
            user_agent=user_agent,
            user_id=user.id,
            tenant_id=user.tenant_id
        )
        db.add(new_refresh_token)
        # save in log
        new_log = Log(
            user_id=user.id,
            user_name=user.username,
            action="login",
            tenant_id=user.tenant_id,
            data=json.dumps({
                "ip": ip_address,
                "user_agent": user_agent,
                "method": request.method,
            })
        )
        db.add(new_log)
        await db.commit()
        
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Đăng nhập thành công",
            data={
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer"
            },
            status_code=ResponseStatusCode.OK
        )

    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )

    except Exception as e:
        import traceback
        error_msg = str(e)
        # Ensure error message doesn't contain UUID objects
        try:
            # Try to serialize error message to catch any UUID
            import json as json_module
            json_module.dumps({"error": error_msg})
        except (TypeError, ValueError):
            error_msg = "Lỗi không xác định"
        print(f"Login error: {error_msg}")
        print(traceback.format_exc())
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            message="Đã xảy ra lỗi không mong muốn từ Server hãy thử lại sau",
            data=error_msg,
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
    
async def logout(request, db: AsyncSession):
    try:
        user_id = verify_token(request)
        query = delete(RefreshToken).where(
        RefreshToken.user_id == user_id
        )
        await db.execute(query)
        await db.commit()
    
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Đăng xuất thành công",
            data=None,
            status_code=ResponseStatusCode.OK
        )
    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            message=f"Đã xảy ra lỗi: {str(e)}",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )

async def get_access_token(request, db: AsyncSession):
    try:
        from jose import jwt
        from app.core.config.app_config import settings
        
        # Get token from header
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Token không hợp lệ",
                data=None,
                status_code=ResponseStatusCode.UNAUTHORIZED
            )
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return api_response(
                status=ResponseStatus.ERROR,
                message="Token không hợp lệ",
                data=None,
                status_code=ResponseStatusCode.UNAUTHORIZED
            )
        token = parts[1]
        
        # Decode token to get user_id and token_version
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("token_type") != "refresh":
            return api_response(
                status=ResponseStatus.ERROR,
                message="Token không hợp lệ",
                data=None,
                status_code=ResponseStatusCode.UNAUTHORIZED
            )
        user_id = payload.get("user_id")
        token_version_from_token = payload.get("token_version")
        
        if not user_id:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Token không hợp lệ",
                data=None,
                status_code=ResponseStatusCode.UNAUTHORIZED
            )
        
        # Check user and token_version
        query = select(User).where(and_(User.id == user_id, User.is_active == 1))
        result = await db.execute(query)
        user = result.scalar_one_or_none()
        if not user:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Người dùng không tồn tại",
                data=None,
                status_code=ResponseStatusCode.UNAUTHORIZED
            )
        
        # Check token_version - if token version doesn't match, token is invalid
        user_token_version = user.token_version if hasattr(user, 'token_version') else 0
        if token_version_from_token is None or token_version_from_token != user_token_version:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Token đã bị vô hiệu hóa",
                data=None,
                status_code=ResponseStatusCode.UNAUTHORIZED
            )
        
        query = select(RefreshToken).where(
            RefreshToken.user_id == user_id,
        )
        result = await db.execute(query)
        refresh_token = result.scalar_one_or_none()
        if not refresh_token:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Token không hợp lệ",
                data=None,
                status_code=ResponseStatusCode.UNAUTHORIZED
            )
        current_time = datetime.now(timezone.utc)
        expired_at = await db.scalar(select(RefreshToken.expired_at).where(RefreshToken.id == refresh_token.id))
        if not expired_at or expired_at < current_time:
            return api_response(
                status=ResponseStatus.ERROR,
                message="Token đã hết hạn",
                data=None,
                status_code=ResponseStatusCode.UNAUTHORIZED
            )
        
        # Include token_version in token payload
        token_version = user.token_version if hasattr(user, 'token_version') else 0
        access_token = create_access_token(data={
            "user_id": str(user.id), 
            "tenant_id": str(user.tenant_id) if user.tenant_id else None,
            "token_version": token_version
        })
        refresh_token_new = create_refresh_token(data={
            "user_id": str(user.id), 
            "tenant_id": str(user.tenant_id) if user.tenant_id else None,
            "token_version": token_version
        })
        return api_response(
            status=ResponseStatus.SUCCESS,
            message="Lấy access token thành công",
            data={
                "access_token": access_token,
                "refresh_token": refresh_token_new,
                "token_type": "bearer"
            },
            status_code=ResponseStatusCode.OK
        )
    except SQLAlchemyError as e:
        await db.rollback()
        return api_response(
            status=ResponseStatus.ERROR,
            message="Lỗi truy vấn cơ sở dữ liệu",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )
    except Exception as e:
        return api_response(
            status=ResponseStatus.ERROR,
            message="Đã xảy ra lỗi không mong muốn từ Server hãy thử lại sau",
            data=str(e),
            status_code=ResponseStatusCode.INTERNAL_SERVER_ERROR
        )  


# luồng đăng nhập username, password, name_tenant khi login vào hệ thống, không cho phép tạo 2 người cùng tên trong cùng 1 tenant