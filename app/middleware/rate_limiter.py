from fastapi import FastAPI, Request, HTTPException
from typing import Optional
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.config.app_config import settings
from app.core.redis.redis_config import RedisHelper

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI):
        super().__init__(app)
        self.window = settings.RATE_LIMIT_WINDOW
        self.limit = settings.RATE_LIMIT_LIMIT

    async def dispatch(self, request: Request, call_next):
        # Lấy định danh của user (có thể là IP hoặc user_id từ token)
        client_id = request.client.host
        if "authorization" in request.headers:
            # Nếu có token, sử dụng user_id từ token
            # implement hàm get_user_id_from_token tùy theo cấu trúc token 
            client_id = await self.get_user_id_from_token(request.headers["authorization"])

        # Tạo key cho Redis
        redis_key = f"rate_limit:{client_id}"
        
        # Đếm số request trong cửa sổ thời gian
        requests = await RedisHelper.increment(redis_key, expire_seconds=self.window)
        
        # Kiểm tra giới hạn
        if requests > self.limit:
            # Lấy thời gian còn lại của key
            remaining_ttl = await RedisHelper.get_key(redis_key)
            raise HTTPException(
                status_code=429,
                detail={
                    "message": "Quá nhiều yêu cầu. Vui lòng thử lại sau.",
                    "wait_time": remaining_ttl
                }
            )

        response = await call_next(request)
        return response

    async def get_user_id_from_token(self, token: str) -> str:
        # TODO: Implement lấy user_id từ token
        # Implementation mẫu, cần thay đổi theo logic xác thực của ứng dụng
        return token
