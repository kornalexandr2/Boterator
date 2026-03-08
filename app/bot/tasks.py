import asyncio
import json
from datetime import timedelta

from aiogram import Bot
from loguru import logger
from sqlalchemy import select

from app.api.common import activate_user_access, resolve_runtime_settings, utcnow
from app.config import settings
from app.database.models import Payment, Subscription, Tariff, User
from app.database.session import async_session
from app.payments.base import build_payment_provider
from app.services.access import revoke_user_from_inaccessible_chats

SUBSCRIPTION_CHECK_INTERVAL_SECONDS = 15 * 60


async def _notify_expiring(bot: Bot, session) -> None:
    now = utcnow()
    stmt = select(Subscription).where(
        Subscription.is_active.is_(True),
        Subscription.end_date.is_not(None),
        Subscription.end_date <= now + timedelta(days=4),
    )
    subscriptions = (await session.execute(stmt)).scalars().all()
    for sub in subscriptions:
        if not sub.end_date:
            continue
        days_left = (sub.end_date.date() - now.date()).days
        if days_left not in {3, 1, 0}:
            continue
        field_name = {3: "notified_3d_at", 1: "notified_1d_at", 0: "notified_0d_at"}[days_left]
        if getattr(sub, field_name):
            continue
        try:
            await bot.send_message(sub.user_id, f"Подписка истекает через {days_left} дн. Не забудьте продлить доступ.")
            setattr(sub, field_name, now)
        except Exception as exc:
            logger.warning(f"Failed to notify user {sub.user_id}: {exc}")


async def _attempt_recurring_charge(session, bot: Bot, sub: Subscription) -> bool:
    if not sub.auto_renew_enabled or not sub.recurring_token or not sub.renewal_provider:
        return False

    tariff = (await session.execute(select(Tariff).where(Tariff.id == sub.tariff_id))).scalar_one_or_none()
    user = (await session.execute(select(User).where(User.telegram_id == sub.user_id))).scalar_one_or_none()
    if not tariff or not user:
        return False

    runtime_settings = await resolve_runtime_settings(session)
    provider = build_payment_provider(
        sub.renewal_provider,
        yoomoney_receiver=runtime_settings.get("yoomoney_receiver", ""),
        yookassa_shop_id=runtime_settings.get("yookassa_shop_id", ""),
        yookassa_secret_key=runtime_settings.get("yookassa_secret_key", ""),
        sberbank_username=runtime_settings.get("sberbank_username", ""),
        sberbank_password=runtime_settings.get("sberbank_password", ""),
    )
    result = await provider.charge_recurring(
        tariff.price,
        f"Автопродление тарифа {tariff.name}",
        sub.recurring_token,
        {"user_id": sub.user_id, "tariff_id": sub.tariff_id},
    )
    if not result.success:
        logger.warning(f"Recurring charge failed for subscription {sub.id}: {result.error_message}")
        return False

    status = await provider.check_status(result.transaction_id) if result.transaction_id else "success"
    if status != "success":
        logger.warning(f"Recurring charge for subscription {sub.id} returned status {status}")
        return False

    payment = Payment(
        user_id=sub.user_id,
        tariff_id=sub.tariff_id,
        amount=tariff.price,
        provider=sub.renewal_provider,
        status="success",
        transaction_id=result.transaction_id,
        recurring_token=result.recurring_token or sub.recurring_token,
        raw_payload=json.dumps(result.raw, ensure_ascii=False, default=str),
    )
    session.add(payment)
    await session.flush()
    await activate_user_access(session, bot, user=user, tariff=tariff, paid=True, payment=payment)
    return True


def _can_use_grace_period(sub: Subscription, grace_period_days: int) -> bool:
    if grace_period_days <= 0:
        return False
    return bool(sub.auto_renew_enabled and sub.recurring_token and sub.renewal_provider)


async def _deactivate_subscription(bot: Bot, session, sub: Subscription) -> None:
    sub.is_active = False
    sub.in_grace_period = False
    sub.grace_end_date = None
    try:
        await bot.send_message(sub.user_id, "Подписка истекла. Доступ к ресурсам закрыт.")
    except Exception as exc:
        logger.warning(f"Failed to notify expired user {sub.user_id}: {exc}")
    await session.flush()
    await revoke_user_from_inaccessible_chats(bot, session, sub.user_id)


async def _handle_expired(bot: Bot, session) -> None:
    now = utcnow()
    runtime_settings = await resolve_runtime_settings(session)
    grace_period_days = int(runtime_settings.get("grace_period_days") or 0)
    stmt = select(Subscription).where(
        Subscription.is_active.is_(True),
        Subscription.end_date.is_not(None),
        Subscription.end_date < now,
    )
    expired_subscriptions = (await session.execute(stmt)).scalars().all()
    for sub in expired_subscriptions:
        user = (await session.execute(select(User).where(User.telegram_id == sub.user_id))).scalar_one_or_none()
        if user and (user.is_admin or user.telegram_id in settings.bot.admin_ids):
            continue

        grace_available = _can_use_grace_period(sub, grace_period_days)
        if grace_available and not sub.in_grace_period:
            renewed = await _attempt_recurring_charge(session, bot, sub)
            if renewed:
                continue
            sub.in_grace_period = True
            sub.grace_end_date = now + timedelta(days=grace_period_days)
            sub.renewal_failed_at = now
            try:
                await bot.send_message(
                    sub.user_id,
                    f"Автопродление не прошло. Включен grace period на {grace_period_days} дн.",
                )
            except Exception as exc:
                logger.warning(f"Failed to notify grace period for {sub.user_id}: {exc}")
            continue

        if grace_available and sub.in_grace_period and sub.grace_end_date and sub.grace_end_date >= now:
            continue

        await _deactivate_subscription(bot, session, sub)


async def check_subscriptions(bot: Bot):
    while True:
        try:
            logger.info("Running subscription check task...")
            if not async_session:
                logger.warning("Subscription task skipped because DB session factory is unavailable.")
            else:
                async with async_session() as session:
                    await _notify_expiring(bot, session)
                    await _handle_expired(bot, session)
                    await session.commit()
        except Exception as exc:
            logger.error(f"Error in subscription check task: {exc}")
        await asyncio.sleep(SUBSCRIPTION_CHECK_INTERVAL_SECONDS)


def start_background_tasks(bot: Bot):
    asyncio.create_task(check_subscriptions(bot))
    logger.info("Background tasks started.")