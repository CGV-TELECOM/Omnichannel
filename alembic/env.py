from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine
from alembic import context
from app.core.config.database import Base
from app.db.models import *
from app.core.config.app_config import settings

# Alembic configuration
config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

# Database URL and metadata
DATABASE_URL = settings.DATABASE_URL
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url") or DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode with an AsyncEngine."""
    engine = create_async_engine(DATABASE_URL, echo=True, future=True)

    # Use engine.begin() to auto-commit transactions
    async with engine.begin() as connection:
        def sync_run_migrations(sync_connection):
            context.configure(
                connection=sync_connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            context.run_migrations()

        await connection.run_sync(sync_run_migrations)

    await engine.dispose()


# Execute migrations based on mode
if context.is_offline_mode():
    run_migrations_offline()
else:
    import asyncio
    asyncio.run(run_migrations_online())