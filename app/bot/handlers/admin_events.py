from aiogram import Router, types, F
from aiogram.enums import ChatMemberStatus
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.database.models import ManagedChat

router = Router()

def check_bot_permissions(member: types.ChatMemberAdministrator | types.ChatMemberOwner):
    """Checks if the bot has all required permissions to manage the chat."""
    missing = []
    
    # Critical for kicking non-paying users
    if not member.can_restrict_members:
        missing.append("Исключение участников (can_restrict_members)")
    
    # Critical for generating invite links
    if not member.can_invite_users:
        missing.append("Пригласительные ссылки (can_invite_users)")
        
    return missing

@router.my_chat_member()
async def on_my_chat_member_update(update: types.ChatMemberUpdated, session: AsyncSession):
    """Triggered when bot's status in a chat changes."""
    chat = update.chat
    new_member = update.new_chat_member
    new_status = new_member.status
    
    logger.info(f"Bot status updated in {chat.type} '{chat.title}' ({chat.id}): {new_status}")

    if new_status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
        # Bot is admin, check permissions
        missing = check_bot_permissions(new_member)
        permissions_ok = len(missing) == 0
        missing_text = ", ".join(missing) if missing else None

        stmt = select(ManagedChat).where(ManagedChat.chat_id == chat.id)
        res = await session.execute(stmt)
        managed_chat = res.scalar_one_or_none()
        
        invite_link = None
        if permissions_ok:
            try:
                link_obj = await update.bot.create_chat_invite_link(chat.id, name="Boterator Auto Link")
                invite_link = link_obj.invite_link
            except Exception as e:
                logger.warning(f"Could not create invite link for {chat.id}: {e}")

        if managed_chat:
            managed_chat.title = chat.title or "Untitled"
            managed_chat.is_active = True
            managed_chat.permissions_ok = permissions_ok
            managed_chat.missing_permissions = missing_text
            if invite_link:
                managed_chat.invite_link = invite_link
        else:
            new_managed = ManagedChat(
                chat_id=chat.id,
                title=chat.title or "Untitled",
                invite_link=invite_link,
                is_active=True,
                permissions_ok=permissions_ok,
                missing_permissions=missing_text
            )
            session.add(new_managed)
        
        logger.info(f"Managed chat updated: {chat.title}. Permissions OK: {permissions_ok}")

    elif new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED, ChatMemberStatus.MEMBER]:
        await session.execute(delete(ManagedChat).where(ManagedChat.chat_id == chat.id))
        logger.info(f"Removed managed chat: {chat.title}")

    await session.commit()
