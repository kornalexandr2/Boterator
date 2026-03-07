from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone

from aiogram.types import BufferedInputFile
from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import ManagedChat, Payment, Subscription, SystemSetting, Tariff, TariffResource, User
from app.database.session import async_session
from app.security.twa import TwaAuthError, validate_init_data
from app.services.access import (
    get_tariff_chat_ids,
    is_staff_role,
    resolve_user_role,
    upsert_user_from_twa,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_db_session():
    if not async_session:
        logger.warning("DB is unavailable. TWA API will respond with controlled errors.")
        yield None
        return
    async with async_session() as session:
        yield session


def db_unavailable_response() -> JSONResponse:
    return JSONResponse({"status": "error", "message": "Database is unavailable"}, status_code=503)


def forbidden_response(message: str = "Недостаточно прав") -> JSONResponse:
    return JSONResponse({"status": "error", "message": message}, status_code=403)


def bad_request_response(message: str) -> JSONResponse:
    return JSONResponse({"status": "error", "message": message}, status_code=400)


def extract_init_data(request: Request, payload: dict | None = None) -> str:
    payload = payload or {}
    return (
        request.headers.get("X-Telegram-Init-Data")
        or payload.get("init_data")
        or request.query_params.get("init_data")
        or ""
    )


async def authenticate_request(request: Request, db: AsyncSession | None, payload: dict | None = None):
    if db is None:
        return None, None, None, db_unavailable_response()

    init_data = extract_init_data(request, payload)
    try:
        ctx = validate_init_data(init_data, settings.bot.token)
    except TwaAuthError as exc:
        return None, None, None, JSONResponse({"status": "error", "message": str(exc)}, status_code=401)

    user = await upsert_user_from_twa(db, ctx)
    await db.commit()
    role = resolve_user_role(user, ctx.telegram_id)
    return ctx, user, role, None


async def load_system_settings(db: AsyncSession) -> dict[str, str]:
    rows = (await db.execute(select(SystemSetting))).scalars().all()
    return {row.key: row.value or "" for row in rows}


def get_runtime_defaults() -> dict[str, str]:
    return {
        "payment_mode": "mock" if settings.payments.mock_mode else "yookassa",
        "yoomoney_receiver": settings.payments.yoomoney_receiver,
        "yookassa_shop_id": settings.payments.yookassa_shop_id,
        "yookassa_secret_key": settings.payments.yookassa_secret_key,
        "sberbank_username": settings.payments.sberbank_username,
        "sberbank_password": settings.payments.sberbank_password,
        "offer_url": "",
        "privacy_url": "",
        "grace_period_days": str(settings.bot.grace_period_days),
    }


async def resolve_runtime_settings(db: AsyncSession | None) -> dict[str, str]:
    data = get_runtime_defaults()
    if db is None:
        return data
    data.update(await load_system_settings(db))
    if not (data.get("payment_mode") or "").strip():
        data["payment_mode"] = "mock" if settings.payments.mock_mode else "yookassa"
    return data


def chat_to_dict(chat: ManagedChat, assigned_tariff_ids: set[int] | None = None) -> dict:
    return {
        "chat_id": chat.chat_id,
        "title": chat.title,
        "invite_link": chat.invite_link,
        "is_active": bool(chat.is_active),
        "permissions_ok": bool(chat.permissions_ok),
        "missing_permissions": chat.missing_permissions,
        "protect_content_enabled": bool(chat.protect_content_enabled),
        "assigned_tariff_ids": sorted(assigned_tariff_ids or set()),
    }


def tariff_to_dict(tariff: Tariff, resource_ids: list[int] | None = None) -> dict:
    return {
        "id": tariff.id,
        "name": tariff.name,
        "description": tariff.description or "",
        "price": tariff.price,
        "duration_days": tariff.duration_days,
        "is_trial": bool(tariff.is_trial),
        "is_hidden": bool(tariff.is_hidden),
        "require_email": bool(tariff.require_email),
        "require_phone": bool(tariff.require_phone),
        "resource_ids": resource_ids or [],
    }


async def get_tariff_resource_map(db: AsyncSession) -> dict[int, list[int]]:
    rows = (await db.execute(select(TariffResource.tariff_id, TariffResource.chat_id))).all()
    data: dict[int, list[int]] = {}
    for tariff_id, chat_id in rows:
        data.setdefault(int(tariff_id), []).append(int(chat_id))
    return data


async def update_user_contacts(user: User, email: str | None = None, phone: str | None = None) -> None:
    if email:
        user.email = email
    if phone:
        user.phone = phone


async def activate_user_access(
    db: AsyncSession,
    bot,
    *,
    user: User,
    tariff: Tariff,
    paid: bool,
    payment: Payment | None = None,
) -> None:
    now = utcnow()
    end_date = None if int(tariff.duration_days) == 0 else now + timedelta(days=int(tariff.duration_days))
    active_sub = (
        await db.execute(
            select(Subscription).where(
                Subscription.user_id == user.telegram_id,
                Subscription.tariff_id == tariff.id,
                Subscription.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if active_sub:
        active_sub.start_date = now
        active_sub.end_date = end_date
        active_sub.in_grace_period = False
        active_sub.grace_end_date = None
    else:
        active_sub = Subscription(
            user_id=user.telegram_id,
            tariff_id=tariff.id,
            start_date=now,
            end_date=end_date,
            is_active=True,
        )
        db.add(active_sub)

    if payment:
        active_sub.auto_renew_enabled = bool(payment.recurring_token)
        active_sub.recurring_token = payment.recurring_token
        active_sub.renewal_provider = payment.provider if payment.recurring_token else None
        await update_user_contacts(user, payment.contact_email, payment.contact_phone)

    await db.commit()

    if not bot:
        return

    chat_ids = await get_tariff_chat_ids(db, tariff.id)
    chats = []
    if chat_ids:
        chats = list((await db.execute(select(ManagedChat).where(ManagedChat.chat_id.in_(chat_ids)))).scalars().all())
    links = "\n".join([f"- <a href='{chat.invite_link}'>{chat.title}</a>" for chat in chats if chat.invite_link])
    until = "без ограничения" if end_date is None else end_date.strftime("%d.%m.%Y")
    title = "Оплата подтверждена" if paid else "Триал активирован"
    if not links:
        links = "Ресурсы пока не привязаны к тарифу. Администратор настроит доступ позже."
    await bot.send_message(
        user.telegram_id,
        f"<b>{title}</b>\nТариф: {tariff.name}\nДоступ до: {until}\n\n<b>Ресурсы:</b>\n{links}",
    )

async def finalize_successful_payment(db: AsyncSession, request: Request, payment: Payment) -> dict:
    tariff = payment.tariff or (
        (await db.execute(select(Tariff).where(Tariff.id == payment.tariff_id))).scalar_one_or_none() if payment.tariff_id else None
    )
    if not tariff:
        return {"status": "error", "message": "Тариф платежа не найден"}

    user = (await db.execute(select(User).where(User.telegram_id == payment.user_id))).scalar_one_or_none()
    if not user:
        return {"status": "error", "message": "Пользователь платежа не найден"}

    bot = request.app.state.bot
    payment.status = "success"
    await db.commit()
    await activate_user_access(db, bot, user=user, tariff=tariff, paid=True, payment=payment)
    return {"status": "ok", "payment_status": "success", "message": "Платеж подтвержден, доступ выдан"}


async def run_broadcast_task(bot, user_ids: list[int], text: str) -> None:
    success = 0
    failed = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, text)
            success += 1
        except Exception as exc:
            failed += 1
            logger.warning(f"Broadcast send failed for {uid}: {exc}")
    logger.info(f"Broadcast finished: {success} ok, {failed} failed")


async def ensure_staff(role: str, admin_only: bool = False, super_only: bool = False):
    if super_only and role != "super_admin":
        return forbidden_response("Только super admin может выполнять это действие")
    if admin_only and role not in {"super_admin", "admin"}:
        return forbidden_response("Только администратор может выполнять это действие")
    if not admin_only and not super_only and not is_staff_role(role):
        return forbidden_response()
    return None


async def serialize_user_list(db: AsyncSession) -> list[dict]:
    users = (await db.execute(select(User).order_by(User.created_at.desc()).limit(200))).scalars().all()
    if not users:
        return []

    user_ids = [user.telegram_id for user in users]
    now = utcnow()
    subs = (
        await db.execute(
            select(Subscription).where(
                Subscription.user_id.in_(user_ids),
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
        )
    ).scalars().all()
    tariffs = {tariff.id: tariff.name for tariff in (await db.execute(select(Tariff))).scalars().all()}
    active_by_user: dict[int, list[Subscription]] = {}
    for sub in subs:
        active_by_user.setdefault(int(sub.user_id), []).append(sub)

    data = []
    for user in users:
        role = resolve_user_role(user, user.telegram_id)
        active_subs = active_by_user.get(int(user.telegram_id), [])
        data.append(
            {
                "telegram_id": user.telegram_id,
                "username": user.username or f"ID {user.telegram_id}",
                "full_name": f"{user.first_name or ''} {user.last_name or ''}".strip(),
                "email": user.email,
                "phone": user.phone,
                "role": role,
                "is_admin": role in {"super_admin", "admin"},
                "is_super": role == "super_admin",
                "is_moderator": role == "moderator",
                "has_active_sub": bool(active_subs),
                "active_tariffs": [tariffs.get(sub.tariff_id, f"#{sub.tariff_id}") for sub in active_subs],
                "is_eternal": bool(user.is_eternal),
            }
        )
    return data


async def build_users_csv(db: AsyncSession) -> BufferedInputFile:
    rows = await serialize_user_list(db)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Telegram ID", "Username", "Email", "Phone", "Role", "Active tariffs", "Eternal"])
    for row in rows:
        writer.writerow(
            [
                row["telegram_id"],
                row["username"],
                row["email"],
                row["phone"],
                row["role"],
                "; ".join(row["active_tariffs"]),
                row["is_eternal"],
            ]
        )
    return BufferedInputFile(output.getvalue().encode("utf-8"), filename="users.csv")


def dump_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)
