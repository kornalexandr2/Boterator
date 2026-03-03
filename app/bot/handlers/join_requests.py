from aiogram import Router, types
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from loguru import logger
from app.database.models import User, Subscription
from datetime import datetime, timezone

router = Router()

@router.chat_join_request()
async def process_join_request(update: types.ChatJoinRequest, session: AsyncSession | None):
    logger.info(f"Join request received from user {update.from_user.id} for chat {update.chat.id}")
    
    if not session:
        logger.warning(f"No DB session, approving join request for {update.from_user.id} by default (Graceful degradation).")
        try:
            await update.approve()
        except Exception as e:
            logger.error(f"Failed to approve join request: {e}")
        return

    # Check user in DB
    stmt = select(User).where(User.telegram_id == update.from_user.id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    
    # Check if user is eternal (already in chat before bot was added)
    if user and user.is_eternal:
         logger.info(f"User {update.from_user.id} is eternal. Approving.")
         try:
             await update.approve()
         except Exception as e:
             logger.error(f"Failed to approve eternal user: {e}")
         return
         
    # Check active subscriptions
    has_active_sub = False
    if user:
         sub_stmt = select(Subscription).where(
             Subscription.user_id == user.telegram_id,
             Subscription.is_active == True,
             (Subscription.end_date == None) | (Subscription.end_date >= datetime.now(timezone.utc))
         )
         sub_result = await session.execute(sub_stmt)
         active_subs = sub_result.scalars().all()
         
         if active_subs:
             has_active_sub = True

    if has_active_sub:
        logger.info(f"User {update.from_user.id} has active subscription. Approving.")
        try:
             await update.approve()
        except Exception as e:
             logger.error(f"Failed to approve subscribed user: {e}")
    else:
        logger.info(f"User {update.from_user.id} has no active subscriptions. Declining or ignoring.")
        try:
             # Option 1: Decline
             # await update.decline()
             # Option 2: Send message to bot PM asking to pay
             await update.bot.send_message(
                 update.from_user.id,
                 "У вас нет активной подписки для доступа к этому каналу. Пожалуйста, оформите тариф."
             )
             await update.decline()
        except Exception as e:
             logger.error(f"Failed to process rejected join request: {e}")
