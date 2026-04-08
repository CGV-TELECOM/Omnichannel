from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import event
from app.core.config.app_config import settings

# Lấy DATABASE_URL từ settings
DATABASE_URL = settings.DATABASE_URL

# Tạo async engine
engine = create_async_engine(DATABASE_URL, echo=True)

# Tạo session maker cho async
async_session_maker = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Base cho ORM Models
Base = declarative_base()

# Dependency để inject session vào route
async def get_db():
    async with async_session_maker() as session:
        yield session