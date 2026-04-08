from fastapi import APIRouter, HTTPException
from app.core.redis.redis_config import RedisHelper
from typing import Optional, Dict
import json

router = APIRouter(prefix="/cache", tags=["cache"])

@router.get("/user/{user_id}")
async def get_user_data(user_id: str) -> Dict:
    """
    Ví dụ về API sử dụng Redis cache
    """
    # Thử lấy dữ liệu từ cache trước
    cache_key = f"user:{user_id}"
    cached_data = await RedisHelper.get_key(cache_key)
    
    if cached_data:
        return json.loads(cached_data)
    
    # Nếu không có trong cache, lấy từ database (giả lập)
    user_data = {
        "id": user_id,
        "name": "Example User",
        "email": "user@example.com"
    }
    
    # Lưu vào cache với thời gian hết hạn là 1 giờ (3600 giây)
    await RedisHelper.set_key(
        cache_key,
        json.dumps(user_data),
        expire_seconds=3600
    )
    
    return user_data

@router.post("/user/{user_id}")
async def update_user_data(user_id: str, data: Dict) -> Dict:
    """
    Ví dụ về API cập nhật dữ liệu và invalidate cache
    """
    # Cập nhật dữ liệu trong database (giả lập)
    
    # Xóa cache để lần get tiếp theo sẽ lấy dữ liệu mới
    cache_key = f"user:{user_id}"
    await RedisHelper.delete_key(cache_key)
    
    return {"message": "Updated successfully"}

@router.get("/stats/{user_id}")
async def get_user_stats(user_id: str) -> Dict:
    """
    Ví dụ về API sử dụng Redis Hash
    """
    hash_key = f"user_stats:{user_id}"
    
    # Lấy thống kê từ Redis Hash
    stats = await RedisHelper.get_hash(hash_key)
    
    if not stats:
        # Nếu chưa có, tạo dữ liệu mẫu
        stats = {
            "login_count": "1",
            "last_login": "2024-03-20",
            "total_actions": "10"
        }
        await RedisHelper.set_hash(hash_key, stats, expire_seconds=86400)  # Hết hạn sau 24h
    
    return stats 