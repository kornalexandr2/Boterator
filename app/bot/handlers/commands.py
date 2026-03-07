from aiogram import Router, types
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.access import mark_user_as_eternal_if_member, resolve_user_role, upsert_user_from_telegram

router = Router()


@router.message(CommandStart())
async def start_cmd(message: types.Message, session: AsyncSession | None):
    logger.info(f"User {message.from_user.id} started the bot.")

    role = "user"
    if session:
        user = await upsert_user_from_telegram(session, message.from_user)
        await mark_user_as_eternal_if_member(message.bot, session, user)
        await session.commit()
        role = resolve_user_role(user, message.from_user.id)
    elif message.from_user.id in settings.bot.admin_ids:
        role = "super_admin"

    builder = InlineKeyboardBuilder()
    builder.button(
        text="Открыть витрину тарифов",
        web_app=types.WebAppInfo(url=f"{settings.app.base_url.rstrip('/')}/twa/store"),
    )
    if role in {"super_admin", "admin", "moderator"}:
        builder.button(
            text="CRM администратора",
            web_app=types.WebAppInfo(url=f"{settings.app.base_url.rstrip('/')}/twa/admin"),
        )
    builder.adjust(1)

    await message.answer(
        "Добро пожаловать в Boterator. Здесь вы можете оформить подписку и управлять доступом к закрытым ресурсам.",
        reply_markup=builder.as_markup(),
    )
