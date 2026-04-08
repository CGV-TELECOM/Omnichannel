from redis import asyncio as aioredis
from typing import Optional
from app.core.config.app_config import settings

class RedisClient:
    _instance: Optional[aioredis.Redis] = None
    _pool: Optional[aioredis.ConnectionPool] = None

    @classmethod
    async def get_instance(cls) -> aioredis.Redis:
        if not cls._instance:
            cls._pool = aioredis.ConnectionPool.from_url(
                settings.REDIS_URL,
                max_connections=50,  # Số kết nối tối đa trong pool
                decode_responses=True  # Tự động decode response từ bytes sang string
            )
            cls._instance = aioredis.Redis(connection_pool=cls._pool)
        return cls._instance

    @classmethod
    async def close(cls):
        if cls._instance:
            await cls._instance.close()
            await cls._pool.disconnect()
            cls._instance = None
            cls._pool = None

# Helper functions để làm việc với Redis
class RedisHelper:
    @staticmethod
    async def set_key(key: str, value: str, expire_seconds: Optional[int] = None):
        redis = await RedisClient.get_instance()
        await redis.set(key, value, ex=expire_seconds)

    @staticmethod
    async def get_key(key: str) -> Optional[str]:
        redis = await RedisClient.get_instance()
        return await redis.get(key)

    @staticmethod
    async def delete_key(key: str):
        redis = await RedisClient.get_instance()
        await redis.delete(key)

    @staticmethod
    async def increment(key: str, expire_seconds: Optional[int] = None) -> int:
        redis = await RedisClient.get_instance()
        value = await redis.incr(key)
        if expire_seconds and value == 1:
            await redis.expire(key, expire_seconds)
        return value

    @staticmethod
    async def set_hash(hash_key: str, mapping: dict, expire_seconds: Optional[int] = None):
        redis = await RedisClient.get_instance()
        await redis.hset(hash_key, mapping=mapping)
        if expire_seconds:
            await redis.expire(hash_key, expire_seconds)

    @staticmethod
    async def get_hash(hash_key: str) -> dict:
        redis = await RedisClient.get_instance()
        return await redis.hgetall(hash_key) 