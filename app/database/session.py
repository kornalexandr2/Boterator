from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from loguru import logger
from app.config import settings

# Graceful degradation logic for the engine
if settings.db.url:
    try:
        engine = create_async_engine(
            settings.db.url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=3600
        )
        async_session = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        logger.info("Database engine initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize database engine: {e}")
        engine = None
        async_session = None
else:
    logger.warning("Database URL is missing. Engine will not be created. Graceful degradation active.")
    engine = None
    async_session = None

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI and Aiogram to get DB session."""
    if not async_session:
        logger.warning("Attempted to access DB, but session is uninitialized. Returning None or raising controlled exception.")
        # Instead of crashing, we yield None and let the caller handle it (e.g. show "Service unavailable" in bot)
        yield None
        return

    async with async_session() as session:
        try:
            yield session
        except Exception as e:
             logger.error(f"Database session error: {e}")
             await session.rollback()
        finally:
            await session.close()

async def init_models():
    """Initializes database tables."""
    if not engine:
         logger.warning("Skipping table initialization as engine is missing.")
         return
         
    from app.database.models import Base
    try:
        async with engine.begin() as conn:
            # For production, alembic migrations should be used.
            # Here we create tables directly per task requirements if no migrations specified.
            await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables verified/created.")
    except Exception as e:
         logger.critical(f"Failed to create database tables: {e}")
