from aiogram import Router, F, types
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from loguru import logger
from app.config import settings
from app.database.models import User

router = Router()

@router.message(CommandStart())
async def start_cmd(message: types.Message, session: AsyncSession | None):
    logger.info(f"User {message.from_user.id} started the bot.")
    
    if session:
         stmt = select(User).where(User.telegram_id == message.from_user.id)
         result = await session.execute(stmt)
         user = result.scalar_one_or_none()
         
         if not user:
             new_user = User(
                 telegram_id=message.from_user.id,
                 username=message.from_user.username,
                 first_name=message.from_user.first_name,
                 last_name=message.from_user.last_name,
                 is_admin=(message.from_user.id in settings.bot.admin_ids)
             )
             session.add(new_user)
             logger.info(f"Registered new user: {message.from_user.id}")

    builder = InlineKeyboardBuilder()
    builder.button(
         text="Открыть витрину тарифов", 
         web_app=types.WebAppInfo(url=f"{settings.app.base_url}/twa/store")
    )
    
    if message.from_user.id in settings.bot.admin_ids:
         builder.button(
             text="⚙️ CRM Администратора",
             web_app=types.WebAppInfo(url=f"{settings.app.base_url}/twa/admin")
         )
    
    builder.adjust(1)
    
    await message.answer(
        "Добро пожаловать в Boterator! 🤖\n\nЗдесь вы можете управлять своими подписками на каналы и группы.",
        reply_markup=builder.as_markup()
    )
