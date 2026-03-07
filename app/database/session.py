from typing import AsyncGenerator

from loguru import logger
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


if settings.db.url:
    try:
        engine = create_async_engine(
            settings.db.url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        logger.info("Database engine initialized.")
    except Exception as exc:
        logger.error(f"Failed to initialize database engine: {exc}")
        engine = None
        async_session = None
else:
    logger.warning("Database URL is missing. Engine will not be created. Graceful degradation active.")
    engine = None
    async_session = None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    if not async_session:
        logger.warning("Attempted to access DB, but session is uninitialized.")
        yield None
        return

    async with async_session() as session:
        try:
            yield session
        except Exception as exc:
            logger.error(f"Database session error: {exc}")
            await session.rollback()
        finally:
            await session.close()


def _add_missing_columns(sync_conn, table_name: str, columns: dict[str, str]) -> None:
    inspector = inspect(sync_conn)
    existing = {col["name"] for col in inspector.get_columns(table_name)}
    for column_name, ddl in columns.items():
        if column_name in existing:
            continue
        sync_conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))
        logger.info(f"Added column {table_name}.{column_name}")


def _run_runtime_migrations(sync_conn) -> None:
    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())

    if "managed_chats" in tables:
        _add_missing_columns(
            sync_conn,
            "managed_chats",
            {
                "permissions_ok": "BOOLEAN DEFAULT 1",
                "missing_permissions": "TEXT NULL",
                "protect_content_enabled": "BOOLEAN DEFAULT 0",
            },
        )

    if "subscriptions" in tables:
        _add_missing_columns(
            sync_conn,
            "subscriptions",
            {
                "in_grace_period": "BOOLEAN DEFAULT 0",
                "grace_end_date": "DATETIME NULL",
                "auto_renew_enabled": "BOOLEAN DEFAULT 0",
                "renewal_provider": "VARCHAR(50) NULL",
                "recurring_token": "VARCHAR(255) NULL",
                "renewal_failed_at": "DATETIME NULL",
                "notified_3d_at": "DATETIME NULL",
                "notified_1d_at": "DATETIME NULL",
                "notified_0d_at": "DATETIME NULL",
            },
        )

    if "payments" in tables:
        _add_missing_columns(
            sync_conn,
            "payments",
            {
                "tariff_id": "INT NULL",
                "contact_email": "VARCHAR(255) NULL",
                "contact_phone": "VARCHAR(255) NULL",
                "refund_id": "VARCHAR(255) NULL",
                "refunded_at": "DATETIME NULL",
                "recurring_token": "VARCHAR(255) NULL",
                "raw_payload": "TEXT NULL",
            },
        )


async def init_models():
    if not engine:
        logger.warning("Skipping table initialization as engine is missing.")
        return

    from app.database.models import Base

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_run_runtime_migrations)
            logger.info("Database tables verified/created.")
    except Exception as exc:
        logger.critical(f"Failed to create database tables: {exc}")
