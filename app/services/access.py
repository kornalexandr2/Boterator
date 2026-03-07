from __future__ import annotations

from datetime import datetime, timezone

from aiogram.enums import ChatMemberStatus
from loguru import logger
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import ManagedChat, Subscription, TariffResource, User
from app.security.twa import TwaUserContext


def resolve_user_role(user: User | None, telegram_id: int) -> str:
    if telegram_id in settings.bot.admin_ids:
        return "super_admin"
    if user and user.is_admin:
        return "admin"
    if user and user.is_moderator:
        return "moderator"
    return "user"


def is_staff_role(role: str) -> bool:
    return role in {"super_admin", "admin", "moderator"}


async def upsert_user_from_twa(db: AsyncSession, ctx: TwaUserContext) -> User:
    user = (await db.execute(select(User).where(User.telegram_id == ctx.telegram_id))).scalar_one_or_none()
    if not user:
        user = User(
            telegram_id=ctx.telegram_id,
            username=ctx.username,
            first_name=ctx.first_name,
            last_name=ctx.last_name,
            is_admin=ctx.telegram_id in settings.bot.admin_ids,
        )
        db.add(user)
        await db.flush()
        return user

    user.username = ctx.username
    user.first_name = ctx.first_name
    user.last_name = ctx.last_name
    if ctx.telegram_id in settings.bot.admin_ids:
        user.is_admin = True
    return user


async def upsert_user_from_telegram(db: AsyncSession, telegram_user) -> User:
    user = (await db.execute(select(User).where(User.telegram_id == telegram_user.id))).scalar_one_or_none()
    if not user:
        user = User(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
            last_name=telegram_user.last_name,
            is_admin=telegram_user.id in settings.bot.admin_ids,
        )
        db.add(user)
        await db.flush()
        return user

    user.username = telegram_user.username
    user.first_name = telegram_user.first_name
    user.last_name = telegram_user.last_name
    if telegram_user.id in settings.bot.admin_ids:
        user.is_admin = True
    return user


async def get_active_subscriptions(db: AsyncSession, user_id: int, now: datetime | None = None) -> list[Subscription]:
    now = now or datetime.now(timezone.utc)
    stmt = select(Subscription).where(
        Subscription.user_id == user_id,
        Subscription.is_active.is_(True),
        or_(
            Subscription.end_date.is_(None),
            Subscription.end_date >= now,
            and_(
                Subscription.in_grace_period.is_(True),
                Subscription.grace_end_date.is_not(None),
                Subscription.grace_end_date >= now,
            ),
        ),
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_tariff_chat_ids(db: AsyncSession, tariff_id: int) -> set[int]:
    stmt = select(TariffResource.chat_id).join(ManagedChat, ManagedChat.chat_id == TariffResource.chat_id).where(
        TariffResource.tariff_id == tariff_id,
        ManagedChat.is_active.is_(True),
    )
    return {int(chat_id) for chat_id in (await db.execute(stmt)).scalars().all()}


async def get_accessible_chat_ids(db: AsyncSession, user: User | None, user_id: int) -> set[int]:
    role = resolve_user_role(user, user_id)
    if (user and user.is_eternal) or is_staff_role(role):
        stmt = select(ManagedChat.chat_id).where(ManagedChat.is_active.is_(True))
        return {int(chat_id) for chat_id in (await db.execute(stmt)).scalars().all()}

    active_subscriptions = await get_active_subscriptions(db, user_id)
    if not active_subscriptions:
        return set()

    tariff_ids = {sub.tariff_id for sub in active_subscriptions}
    stmt = select(TariffResource.chat_id).join(ManagedChat, ManagedChat.chat_id == TariffResource.chat_id).where(
        TariffResource.tariff_id.in_(tariff_ids),
        ManagedChat.is_active.is_(True),
    )
    return {int(chat_id) for chat_id in (await db.execute(stmt)).scalars().all()}


async def get_accessible_chats(db: AsyncSession, user: User | None, user_id: int) -> list[ManagedChat]:
    allowed_chat_ids = await get_accessible_chat_ids(db, user, user_id)
    if not allowed_chat_ids:
        return []
    stmt = select(ManagedChat).where(ManagedChat.chat_id.in_(allowed_chat_ids), ManagedChat.is_active.is_(True))
    return list((await db.execute(stmt)).scalars().all())


async def revoke_user_from_inaccessible_chats(bot, db: AsyncSession, user_id: int) -> None:
    user = (await db.execute(select(User).where(User.telegram_id == user_id))).scalar_one_or_none()
    allowed_chat_ids = await get_accessible_chat_ids(db, user, user_id)
    chats = (await db.execute(select(ManagedChat).where(ManagedChat.is_active.is_(True)))).scalars().all()
    for chat in chats:
        if chat.chat_id in allowed_chat_ids:
            continue
        try:
            member = await bot.get_chat_member(chat.chat_id, user_id)
            if member.status in {ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED}:
                await bot.ban_chat_member(chat.chat_id, user_id)
                await bot.unban_chat_member(chat.chat_id, user_id, only_if_banned=True)
        except Exception as exc:
            logger.warning(f"Failed to revoke access for user {user_id} in chat {chat.chat_id}: {exc}")


async def mark_user_as_eternal_if_member(bot, db: AsyncSession, user: User) -> bool:
    if user.is_eternal:
        return True

    chats = (await db.execute(select(ManagedChat).where(ManagedChat.is_active.is_(True)))).scalars().all()
    for chat in chats:
        try:
            member = await bot.get_chat_member(chat.chat_id, user.telegram_id)
            if member.status not in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
                user.is_eternal = True
                await db.flush()
                return True
        except Exception:
            continue
    return False
