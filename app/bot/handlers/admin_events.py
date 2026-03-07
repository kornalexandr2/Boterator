from aiogram import Router, types
from aiogram.enums import ChatMemberStatus
from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ManagedChat

router = Router()


def check_bot_permissions(member: types.ChatMemberAdministrator | types.ChatMemberOwner) -> list[str]:
    missing = []
    if not getattr(member, "can_restrict_members", False):
        missing.append("Исключение участников")
    if not getattr(member, "can_invite_users", False):
        missing.append("Пригласительные ссылки")
    return missing


@router.my_chat_member()
async def on_my_chat_member_update(update: types.ChatMemberUpdated, session: AsyncSession | None):
    if not session:
        logger.warning("Skipping managed chat sync because DB session is unavailable.")
        return

    chat = update.chat
    new_member = update.new_chat_member
    logger.info(f"Bot status updated in {chat.type} '{chat.title}' ({chat.id}): {new_member.status}")

    if new_member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
        missing = check_bot_permissions(new_member)
        invite_link = None
        if not missing:
            try:
                link_obj = await update.bot.create_chat_invite_link(chat.id, name="Boterator Auto Link")
                invite_link = link_obj.invite_link
            except Exception as exc:
                logger.warning(f"Could not create invite link for {chat.id}: {exc}")

        current = (await session.execute(select(ManagedChat).where(ManagedChat.chat_id == chat.id))).scalar_one_or_none()
        protect_content_enabled = bool(getattr(chat, "has_protected_content", False))
        if current:
            current.title = chat.title or "Untitled"
            current.is_active = True
            current.permissions_ok = len(missing) == 0
            current.missing_permissions = ", ".join(missing) if missing else None
            current.protect_content_enabled = protect_content_enabled
            if invite_link:
                current.invite_link = invite_link
        else:
            session.add(
                ManagedChat(
                    chat_id=chat.id,
                    title=chat.title or "Untitled",
                    invite_link=invite_link,
                    is_active=True,
                    permissions_ok=len(missing) == 0,
                    missing_permissions=", ".join(missing) if missing else None,
                    protect_content_enabled=protect_content_enabled,
                )
            )
        await session.commit()
        return

    if new_member.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED, ChatMemberStatus.MEMBER}:
        await session.execute(delete(ManagedChat).where(ManagedChat.chat_id == chat.id))
        await session.commit()
        logger.info(f"Removed managed chat: {chat.title}")
