from app.db.models import Log
from functools import wraps
from app.core.config.database import get_db
from fastapi import Request, Depends
from app.services.v1.handle_user import get_current_user
import json
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Không dùng substring "key" / "token" quá rộng: sẽ lọc nhầm some_feature_key, monkey, ...
_SENSITIVE_NAME_EXACT = frozenset(
    {"password", "passwd", "secret", "token", "key", "authorization"}
)
# oauth / session style fields
_SENSITIVE_SUFFIX_TOKEN = "_token"

_SENSITIVE_NAME_SUBSTR = (
    "password",
    "passwd",
    "secret",
    "credential",
    "authorization",
    "bearer",
    "access_token",
    "refresh_token",
    "id_token",
    "auth_token",
    "api_token",
    "api_key",
    "secret_key",
    "private_key",
    "client_secret",
    "webhook_secret",
)


def _field_name_is_sensitive(field_name: str) -> bool:
    lower = field_name.lower()
    if lower in _SENSITIVE_NAME_EXACT:
        return True
    if lower.endswith(_SENSITIVE_SUFFIX_TOKEN):
        return True
    return any(part in lower for part in _SENSITIVE_NAME_SUBSTR)


def filter_sensitive_data(data: dict) -> dict:
    """Lọc các trường nhạy cảm khỏi dữ liệu"""
    if not isinstance(data, dict):
        return data

    filtered_data = data.copy()
    for key in data:
        if _field_name_is_sensitive(key):
            filtered_data[key] = "***FILTERED***"
        elif isinstance(data[key], dict):
            filtered_data[key] = filter_sensitive_data(data[key])
        elif isinstance(data[key], list):
            filtered_data[key] = [
                filter_sensitive_data(item) if isinstance(item, dict) else item
                for item in data[key]
            ]
    return filtered_data

def log_user_action(action_name: str):  
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, request: Request | None, db: AsyncSession = Depends(get_db), **kwargs):
            try:
                ip_address = request.client.host
                user_agent = request.headers.get('user-agent')
                # Store original body
                body_bytes = await request.body()
                # Restore body for future use
                async def receive():
                    return {"type": "http.request", "body": body_bytes}
                request._receive = receive

                # Execute original function
                response = await func(*args, request=request, db=db, **kwargs)

                if db:
                    try:
                        # Lấy user từ token
                        user = await get_current_user(request, db)
                        
                        # Chuẩn bị request data an toàn
                        request_data = {
                            "ip": ip_address,
                            "user_agent": user_agent,
                            "method": request.method,
                            "query_params": dict(request.query_params),
                        }
                        
                        try:
                            body_str = body_bytes.decode("utf-8")
                            if body_str:
                                body_data = json.loads(body_str)
                                # Lọc các trường nhạy cảm trước khi lưu log
                                request_data["body"] = filter_sensitive_data(body_data)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            request_data["body"] = "Unable to parse request body"

                        utc_time = datetime.now(timezone.utc)

                        # Ghi log
                        log = Log(
                            user_name=user.username if user else "anonymous",
                            action=action_name,
                            data=json.dumps(request_data, ensure_ascii=False),
                            create_time=utc_time,
                            user_id=user.id if user else None,
                        )
                        db.add(log)
                        await db.commit()
                    except Exception as e:
                        logger.error(f"Error logging user action: {str(e)}")
                        await db.rollback()

                return response
            except Exception as e:
                logger.error(f"Error in request processing: {str(e)}")
                raise

        return wrapper
    return decorator
