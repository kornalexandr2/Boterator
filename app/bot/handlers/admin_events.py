from aiogram import Router, types, F
from aiogram.enums import ChatMemberStatus
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.database.models import ManagedChat

router = Router()

@router.my_chat_member()
async def on_my_chat_member_update(update: types.ChatMemberUpdated, session: AsyncSession):
    """Triggered when bot's status in a chat changes (added as admin, removed, etc)."""
    chat = update.chat
    new_status = update.new_chat_member.status
    
    logger.info(f"Bot status updated in {chat.type} '{chat.title}' ({chat.id}): {new_status}")

    if new_status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
        # Bot is admin, add/update in DB
        stmt = select(ManagedChat).where(ManagedChat.chat_id == chat.id)
        res = await session.execute(stmt)
        managed_chat = res.scalar_one_or_none()
        
        # Try to get or create invite link
        invite_link = None
        try:
            # We need a permanent link or we create a new one
            link_obj = await update.bot.create_chat_invite_link(chat.id, name="Boterator Auto Link")
            invite_link = link_obj.invite_link
        except Exception as e:
            logger.warning(f"Could not create invite link for {chat.id}: {e}")

        if managed_chat:
            managed_chat.title = chat.title or "Untitled"
            managed_chat.is_active = True
            if invite_link:
                managed_chat.invite_link = invite_link
        else:
            new_managed = ManagedChat(
                chat_id=chat.id,
                title=chat.title or "Untitled",
                invite_link=invite_link,
                is_active=True
            )
            session.add(new_managed)
        
        logger.info(f"Added/Updated managed chat: {chat.title}")

    elif new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED, ChatMemberStatus.MEMBER]:
        # Bot is no longer admin, mark as inactive or remove
        await session.execute(delete(ManagedChat).where(ManagedChat.chat_id == chat.id))
        logger.info(f"Removed managed chat: {chat.title}")

    await session.commit()
