import asyncio
from datetime import datetime, timezone, timedelta
from loguru import logger
from sqlalchemy import select, update
from app.database.session import async_session
from app.database.models import Subscription, User
from aiogram import Bot

async def check_subscriptions(bot: Bot):
    """Periodically checks for expiring subscriptions and notifies users."""
    while True:
        try:
            logger.info("Running subscription check task...")
            async with async_session() as session:
                now = datetime.now(timezone.utc)
                
                # 1. Notify users whose subscription ends in 3 days, 1 day
                # (For simplicity, we check specific intervals)
                for days in [3, 1]:
                    target_date = now + timedelta(days=days)
                    # Find active subs ending within the next hour of the target date
                    # In a real app, you'd track if notification was already sent
                    stmt = select(Subscription).where(
                        Subscription.is_active == True,
                        Subscription.end_date >= target_date - timedelta(hours=1),
                        Subscription.end_date <= target_date + timedelta(hours=1)
                    )
                    res = await session.execute(stmt)
                    subs = res.scalars().all()
                    
                    for sub in subs:
                        try:
                            msg = f"⚠️ Ваша подписка истекает через {days} дн. Не забудьте продлить!"
                            await bot.send_message(sub.user_id, msg)
                        except Exception as e:
                            logger.warning(f"Failed to notify user {sub.user_id}: {e}")

                # 2. Process expired subscriptions
                stmt = select(Subscription).where(
                    Subscription.is_active == True,
                    Subscription.end_date < now
                )
                res = await session.execute(stmt)
                expired_subs = res.scalars().all()
                
                for sub in expired_subs:
                    # Check if user is admin before processing expiry
                    user_stmt = select(User).where(User.telegram_id == sub.user_id)
                    user_res = await session.execute(user_stmt)
                    user = user_res.scalar_one_or_none()

                    if user and user.is_admin:
                        logger.info(f"Skipping expiry for admin user {sub.user_id}")
                        continue

                    logger.info(f"Subscription {sub.id} for user {sub.user_id} expired.")
                    # Deactivate sub
                    sub.is_active = False
                    try:
                        await bot.send_message(sub.user_id, "🔴 Ваша подписка истекла. Доступ к закрытым ресурсам ограничен.")
                        # Here you could also kick user from channel/group
                        # await bot.ban_chat_member(chat_id, sub.user_id)
                    except Exception as e:
                        logger.warning(f"Failed to notify/kick user {sub.user_id}: {e}")
                
                await session.commit()
                
        except Exception as e:
            logger.error(f"Error in subscription check task: {e}")
            
        # Run check every 6 hours
        await asyncio.sleep(6 * 3600)

def start_background_tasks(bot: Bot):
    asyncio.create_task(check_subscriptions(bot))
    logger.info("Background tasks started.")
