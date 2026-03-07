from aiogram import Router, types
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.services.access import get_accessible_chat_ids, upsert_user_from_telegram

router = Router()


@router.chat_join_request()
async def process_join_request(update: types.ChatJoinRequest, session: AsyncSession | None):
    logger.info(f"Join request received from user {update.from_user.id} for chat {update.chat.id}")

    if not session:
        logger.warning(f"No DB session, cannot verify access for {update.from_user.id}. Declining safely.")
        try:
            await update.bot.send_message(
                update.from_user.id,
                "Сервис проверки доступа временно недоступен. Попробуйте еще раз позже.",
            )
            await update.decline()
        except Exception as exc:
            logger.error(f"Failed to decline join request without DB: {exc}")
        return

    user = await upsert_user_from_telegram(session, update.from_user)
    await session.commit()
    user = (await session.execute(select(User).where(User.telegram_id == update.from_user.id))).scalar_one_or_none()

    allowed_chat_ids = await get_accessible_chat_ids(session, user, update.from_user.id)
    if user and (user.is_eternal or update.chat.id in allowed_chat_ids):
        logger.info(f"User {update.from_user.id} is allowed for chat {update.chat.id}. Approving.")
        try:
            await update.approve()
        except Exception as exc:
            logger.error(f"Failed to approve join request: {exc}")
        return

    logger.info(f"User {update.from_user.id} has no access to chat {update.chat.id}. Declining.")
    try:
        await update.bot.send_message(
            update.from_user.id,
            "У вас нет активного тарифа для этого ресурса. Откройте витрину и оформите подписку.",
        )
        await update.decline()
    except Exception as exc:
        logger.error(f"Failed to process rejected join request: {exc}")
