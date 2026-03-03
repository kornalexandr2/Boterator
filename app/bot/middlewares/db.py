from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.session import async_session
from loguru import logger

class DbSessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        
        if not async_session:
             logger.warning("DbSessionMiddleware: Database session is not initialized. Injecting None.")
             data['session'] = None
             return await handler(event, data)
             
        async with async_session() as session:
            try:
                data['session'] = session
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception as e:
                logger.error(f"Error in DbSessionMiddleware, rolling back: {e}")
                await session.rollback()
                raise
