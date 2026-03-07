from datetime import datetime, timedelta, timezone
import csv
import io

from aiogram.types import BufferedInputFile
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.admin_events import check_bot_permissions
from app.config import settings
from app.database.models import ManagedChat, Payment, Subscription, SystemSetting, Tariff, User
from app.database.session import async_session
from app.payments.base import build_payment_provider

router = APIRouter(prefix="/twa", tags=["twa"])
templates = Jinja2Templates(directory="app/templates")


async def get_db_session():
    if not async_session:
        logger.warning("DB is unavailable. TWA API will respond with controlled errors.")
        yield None
        return
    async with async_session() as session:
        yield session


def db_unavailable_response() -> JSONResponse:
    return JSONResponse({"status": "error", "message": "Database is unavailable"}, status_code=503)


def chat_to_dict(chat: ManagedChat) -> dict:
    return {
        "chat_id": chat.chat_id,
        "title": chat.title,
        "invite_link": chat.invite_link,
        "is_active": bool(chat.is_active),
        "permissions_ok": bool(chat.permissions_ok),
        "missing_permissions": chat.missing_permissions,
    }


def tariff_to_dict(tariff: Tariff) -> dict:
    return {
        "id": tariff.id,
        "name": tariff.name,
        "description": tariff.description,
        "price": tariff.price,
        "duration_days": tariff.duration_days,
        "is_trial": bool(tariff.is_trial),
        "is_hidden": bool(tariff.is_hidden),
        "require_email": bool(tariff.require_email),
        "require_phone": bool(tariff.require_phone),
    }


async def activate_user_access(
    db: AsyncSession,
    bot,
    *,
    user_id: int,
    tariff: Tariff,
    paid: bool,
) -> None:
    now = datetime.now(timezone.utc)
    end_date = None if int(tariff.duration_days) == 0 else now + timedelta(days=int(tariff.duration_days))

    active_sub = (
        await db.execute(
            select(Subscription).where(Subscription.user_id == user_id, Subscription.tariff_id == tariff.id, Subscription.is_active.is_(True))
        )
    ).scalar_one_or_none()

    if active_sub:
        active_sub.start_date = now
        active_sub.end_date = end_date
    else:
        db.add(
            Subscription(
                user_id=user_id,
                tariff_id=tariff.id,
                start_date=now,
                end_date=end_date,
                is_active=True,
            )
        )

    await db.commit()

    links = "\n".join(
        [
            f"🔹 <a href='{c.invite_link}'>{c.title}</a>"
            for c in (await db.execute(select(ManagedChat).where(ManagedChat.is_active.is_(True)))).scalars().all()
            if c.invite_link
        ]
    )
    title = "Оплата подтверждена" if paid else "Триал активирован"
    until = "без ограничения" if end_date is None else end_date.strftime("%d.%m.%Y")
    await bot.send_message(user_id, f"✅ <b>{title}</b>\nДоступ до: {until}\n\n<b>Ссылки:</b>\n{links}")


async def run_broadcast_task(bot, user_ids: list[int], text: str):
    success = 0
    failed = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, text)
            success += 1
        except Exception as exc:
            failed += 1
            logger.warning(f"Broadcast send failed for {uid}: {exc}")
    logger.info(f"Broadcast: {success} ok, {failed} failed")


@router.get("/store", response_class=HTMLResponse)
async def get_store(request: Request):
    return templates.TemplateResponse("client/store.html", {"request": request})


@router.get("/admin", response_class=HTMLResponse)
async def get_admin_crm(request: Request):
    return templates.TemplateResponse("admin/crm.html", {"request": request})


@router.get("/managed-chats")
async def list_managed_chats(db: AsyncSession | None = Depends(get_db_session)):
    if db is None:
        return []
    stmt = select(ManagedChat)
    res = await db.execute(stmt)
    return [chat_to_dict(c) for c in res.scalars().all()]


@router.get("/stats")
async def get_stats(db: AsyncSession | None = Depends(get_db_session)):
    if db is None:
        return db_unavailable_response()
    try:
        month_ago = datetime.now(timezone.utc) - timedelta(days=30)
        rev = (
            await db.execute(
                select(func.sum(Payment.amount)).where(Payment.status == "success", Payment.created_at >= month_ago)
            )
        ).scalar() or 0
        new_u = (await db.execute(select(func.count(User.telegram_id)).where(User.created_at >= month_ago))).scalar() or 0
        act_s = (await db.execute(select(func.count(Subscription.id)).where(Subscription.is_active.is_(True)))).scalar() or 0
        tot_u = (await db.execute(select(func.count(User.telegram_id)))).scalar() or 0
        return {
            "monthly_revenue": f"{rev:,.0f} ₽",
            "new_users": f"+{new_u}",
            "active_subscriptions": act_s,
            "total_users": tot_u,
        }
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@router.get("/tariffs")
async def list_tariffs(db: AsyncSession | None = Depends(get_db_session)):
    if db is None:
        return []
    tariffs = (await db.execute(select(Tariff).order_by(Tariff.id))).scalars().all()
    return [tariff_to_dict(t) for t in tariffs]


@router.post("/tariffs")
async def save_tariff(request: Request, db: AsyncSession | None = Depends(get_db_session)):
    if db is None:
        return db_unavailable_response()
    data = await request.json()
    tid = data.get("id")
    try:
        values = {
            "name": data["name"],
            "description": data.get("description", ""),
            "price": float(data["price"]),
            "duration_days": int(data["duration_days"]),
            "is_trial": bool(data.get("is_trial", False)),
            "is_hidden": bool(data.get("is_hidden", False)),
            "require_email": bool(data.get("require_email", False)),
            "require_phone": bool(data.get("require_phone", False)),
        }
        if tid:
            await db.execute(update(Tariff).where(Tariff.id == tid).values(**values))
        else:
            db.add(Tariff(**values))
        await db.commit()
        return {"status": "ok"}
    except Exception as e:
        await db.rollback()
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@router.delete("/tariffs/{tariff_id}")
async def delete_tariff(tariff_id: int, db: AsyncSession | None = Depends(get_db_session)):
    if db is None:
        return db_unavailable_response()
    await db.execute(delete(Tariff).where(Tariff.id == tariff_id))
    await db.commit()
    return {"status": "ok"}


@router.get("/users")
async def list_users(db: AsyncSession | None = Depends(get_db_session)):
    if db is None:
        return []
    users = (await db.execute(select(User).order_by(User.created_at.desc()).limit(100))).scalars().all()
    res_list = []
    for u in users:
        active = (
            await db.execute(
                select(Subscription).where(Subscription.user_id == u.telegram_id, Subscription.is_active.is_(True))
            )
        ).scalar_one_or_none()
        res_list.append(
            {
                "telegram_id": u.telegram_id,
                "username": u.username or f"ID: {u.telegram_id}",
                "full_name": f"{u.first_name or ''} {u.last_name or ''}".strip(),
                "has_active_sub": active is not None,
                "is_admin": u.telegram_id in settings.bot.admin_ids or u.is_admin,
                "is_super": u.telegram_id in settings.bot.admin_ids,
            }
        )
    return res_list


@router.get("/settings")
async def get_system_settings(db: AsyncSession | None = Depends(get_db_session)):
    if db is None:
        return {
            "payment_mode": "mock",
            "yoomoney_receiver": settings.payments.yoomoney_receiver,
            "yookassa_shop_id": settings.payments.yookassa_shop_id,
            "sberbank_username": settings.payments.sberbank_username,
        }
    res = (await db.execute(select(SystemSetting))).scalars().all()
    data = {
        "payment_mode": "mock",
        "yoomoney_receiver": settings.payments.yoomoney_receiver,
        "yookassa_shop_id": settings.payments.yookassa_shop_id,
        "sberbank_username": settings.payments.sberbank_username,
    }
    for s in res:
        data[s.key] = s.value
    return data


@router.post("/settings")
async def update_system_settings(request: Request, db: AsyncSession | None = Depends(get_db_session)):
    if db is None:
        return db_unavailable_response()
    data = await request.json()
    for k, v in data.items():
        s = (await db.execute(select(SystemSetting).where(SystemSetting.key == k))).scalar_one_or_none()
        if s:
            s.value = str(v)
        else:
            db.add(SystemSetting(key=k, value=str(v)))
    await db.commit()
    return {"status": "ok"}


@router.post("/payments/confirm")
async def confirm_payment(request: Request, db: AsyncSession | None = Depends(get_db_session)):
    if db is None:
        return db_unavailable_response()

    data = await request.json()
    transaction_id = data.get("transaction_id")
    user_id = data.get("user_id")
    tariff_id = data.get("tariff_id")
    provider_name = (data.get("provider") or "").strip().lower()

    if not transaction_id or not user_id or not tariff_id:
        return JSONResponse({"status": "error", "message": "transaction_id, user_id и tariff_id обязательны"}, status_code=400)

    payment = (
        await db.execute(select(Payment).where(Payment.transaction_id == transaction_id, Payment.user_id == int(user_id)))
    ).scalar_one_or_none()
    if not payment:
        return JSONResponse({"status": "error", "message": "Платеж не найден"}, status_code=404)

    tariff = (await db.execute(select(Tariff).where(Tariff.id == int(tariff_id)))).scalar_one_or_none()
    if not tariff:
        return JSONResponse({"status": "error", "message": "Тариф не найден"}, status_code=404)

    settings_dict = {s.key: s.value for s in (await db.execute(select(SystemSetting))).scalars().all()}
    mode = provider_name or settings_dict.get("payment_mode", payment.provider)
    provider = build_payment_provider(
        mode,
        yoomoney_receiver=settings_dict.get("yoomoney_receiver", settings.payments.yoomoney_receiver),
        yookassa_shop_id=settings_dict.get("yookassa_shop_id", settings.payments.yookassa_shop_id),
        yookassa_secret_key=settings_dict.get("yookassa_secret_key", settings.payments.yookassa_secret_key),
        sberbank_username=settings_dict.get("sberbank_username", settings.payments.sberbank_username),
        sberbank_password=settings_dict.get("sberbank_password", settings.payments.sberbank_password),
    )

    status = await provider.check_status(transaction_id)
    if status != "success":
        payment.status = "failed" if status == "failed" else "pending"
        await db.commit()
        return {"status": "ok", "payment_status": payment.status, "message": "Платеж пока не подтвержден"}

    payment.status = "success"
    await db.commit()

    bot = request.app.state.bot
    if bot:
        await activate_user_access(db, bot, user_id=int(user_id), tariff=tariff, paid=True)

    return {"status": "ok", "payment_status": "success", "message": "Платеж подтвержден, доступ выдан"}


@router.post("/action")
async def process_twa_action(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession | None = Depends(get_db_session),
):
    try:
        data = await request.json()
        action = data.get("action")
        bot = request.app.state.bot
        if db is None:
            return db_unavailable_response()
        if not bot:
            return JSONResponse({"status": "error", "message": "Бот не запущен"}, status_code=500)

        user_id = data.get("user_id")

        if action == "add_resource":
            chat_id = data.get("chat_id")
            try:
                chat_id = int(chat_id)
                chat = await bot.get_chat(chat_id)
                member = await bot.get_chat_member(chat_id, (await bot.get_me()).id)
                from aiogram.enums import ChatMemberStatus

                if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                    return JSONResponse({"status": "error", "message": "Бот должен быть администратором в этой группе!"})

                missing = check_bot_permissions(member)
                invite_link = None
                if not missing:
                    link_obj = await bot.create_chat_invite_link(chat_id, name="Boterator Manual Add")
                    invite_link = link_obj.invite_link

                stmt = select(ManagedChat).where(ManagedChat.chat_id == chat_id)
                existing = (await db.execute(stmt)).scalar_one_or_none()
                if existing:
                    existing.title = chat.title
                    existing.is_active = True
                    existing.permissions_ok = len(missing) == 0
                    existing.missing_permissions = ", ".join(missing) if missing else None
                    if invite_link:
                        existing.invite_link = invite_link
                else:
                    db.add(
                        ManagedChat(
                            chat_id=chat_id,
                            title=chat.title,
                            is_active=True,
                            permissions_ok=len(missing) == 0,
                            missing_permissions=", ".join(missing) if missing else None,
                            invite_link=invite_link,
                        )
                    )
                await db.commit()
                return {"status": "ok", "message": f"Ресурс '{chat.title}' добавлен!"}
            except Exception as e:
                return JSONResponse(
                    {"status": "error", "message": f"Ошибка: {str(e)}. Убедитесь, что ID верный и бот в группе."}
                )

        if action == "sync_resources":
            chats = (await db.execute(select(ManagedChat))).scalars().all()
            sync_count = 0
            for c in chats:
                try:
                    member = await bot.get_chat_member(c.chat_id, (await bot.get_me()).id)
                    from aiogram.enums import ChatMemberStatus

                    if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                        missing = check_bot_permissions(member)
                        c.permissions_ok = len(missing) == 0
                        c.missing_permissions = ", ".join(missing) if missing else None
                        c.is_active = True
                        sync_count += 1
                    else:
                        c.is_active = False
                except Exception:
                    c.is_active = False
            await db.commit()
            return {"status": "ok", "message": f"Обновлено ресурсов: {sync_count}"}

        if action == "buy":
            tid = data.get("tariff_id")
            tariff = (await db.execute(select(Tariff).where(Tariff.id == tid))).scalar_one_or_none()
            if not tariff:
                return JSONResponse({"status": "error", "message": "Тариф не найден"}, status_code=404)

            if tariff.require_email and not data.get("email"):
                return JSONResponse({"status": "error", "message": "Email обязателен для этого тарифа"}, status_code=400)
            if tariff.require_phone and not data.get("phone"):
                return JSONResponse({"status": "error", "message": "Телефон обязателен для этого тарифа"}, status_code=400)

            if tariff.is_trial:
                trial_exists = (
                    await db.execute(
                        select(Subscription)
                        .join(Tariff)
                        .where(Subscription.user_id == user_id, Tariff.is_trial.is_(True))
                    )
                ).scalar_one_or_none()
                if trial_exists:
                    return JSONResponse({"status": "error", "message": "Триал уже был"}, status_code=400)
                await activate_user_access(db, bot, user_id=user_id, tariff=tariff, paid=False)
                return {"status": "ok", "message": "Триал активирован"}

            settings_dict = {s.key: s.value for s in (await db.execute(select(SystemSetting))).scalars().all()}
            mode = settings_dict.get("payment_mode", "mock")
            provider = build_payment_provider(
                mode,
                yoomoney_receiver=settings_dict.get("yoomoney_receiver", settings.payments.yoomoney_receiver),
                yookassa_shop_id=settings_dict.get("yookassa_shop_id", settings.payments.yookassa_shop_id),
                yookassa_secret_key=settings_dict.get("yookassa_secret_key", settings.payments.yookassa_secret_key),
                sberbank_username=settings_dict.get("sberbank_username", settings.payments.sberbank_username),
                sberbank_password=settings_dict.get("sberbank_password", settings.payments.sberbank_password),
            )
            pay_res = await provider.create_payment(
                tariff.price,
                f"Оплата: {tariff.name}",
                {"user_id": user_id, "tariff_id": tid, "email": data.get("email"), "phone": data.get("phone")},
            )
            if pay_res.success:
                is_mock_mode = mode.strip().lower() == "mock"
                payment_status = "success" if is_mock_mode else "pending"
                db.add(
                    Payment(
                        user_id=user_id,
                        amount=tariff.price,
                        provider=mode,
                        status=payment_status,
                        transaction_id=pay_res.transaction_id,
                    )
                )
                await db.commit()

                if is_mock_mode:
                    await activate_user_access(db, bot, user_id=user_id, tariff=tariff, paid=True)
                    return {"status": "ok", "message": "Оплата подтверждена, доступ выдан"}

                payment_link = pay_res.payment_url or "Платеж создан, ожидается подтверждение"
                await bot.send_message(
                    user_id,
                    f"💳 <b>Оплата: {tariff.name}</b>\nСумма: {tariff.price} ₽\nСсылка: {payment_link}",
                )
                return {
                    "status": "ok",
                    "message": "Инструкция отправлена в ЛС",
                    "transaction_id": pay_res.transaction_id,
                    "provider": mode,
                    "tariff_id": tariff.id,
                }
            return JSONResponse({"status": "error", "message": pay_res.error_message or "Ошибка оплаты"}, status_code=500)

        if action == "promote_admin":
            await db.execute(update(User).where(User.telegram_id == data.get("target_id")).values(is_admin=True))
            await db.commit()
            return {"status": "ok", "message": "Назначен админ"}

        if action == "demote_admin":
            if data.get("target_id") in settings.bot.admin_ids:
                return JSONResponse({"status": "error", "message": "Нельзя снять Super Admin"}, status_code=400)
            await db.execute(update(User).where(User.telegram_id == data.get("target_id")).values(is_admin=False))
            await db.commit()
            return {"status": "ok", "message": "Админ снят"}

        if action == "kick_user":
            target_id = data.get("target_id")
            await db.execute(update(Subscription).where(Subscription.user_id == target_id).values(is_active=False))
            await db.commit()
            for c in (await db.execute(select(ManagedChat).where(ManagedChat.is_active.is_(True)))).scalars().all():
                try:
                    await bot.ban_chat_member(c.chat_id, target_id)
                    await bot.unban_chat_member(c.chat_id, target_id)
                except Exception as exc:
                    logger.warning(f"Failed to remove user {target_id} from chat {c.chat_id}: {exc}")
            try:
                await bot.send_message(target_id, f"🔴 <b>Вы исключены.</b>\nПричина: {data.get('reason')}")
            except Exception as exc:
                logger.warning(f"Failed to notify kicked user {target_id}: {exc}")
            return {"status": "ok", "message": "Исключен"}

        if action == "export_csv":
            users = (await db.execute(select(User))).scalars().all()
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "User", "Email", "Admin", "Date"])
            for u in users:
                writer.writerow([u.telegram_id, u.username, u.email, u.is_admin, u.created_at])
            if not settings.bot.admin_ids:
                return JSONResponse({"status": "error", "message": "ADMIN_IDS is empty"}, status_code=400)
            await bot.send_document(
                settings.bot.admin_ids[0],
                BufferedInputFile(output.getvalue().encode("utf-8"), filename="users.csv"),
                caption="Экспорт",
            )
            return {"status": "ok", "message": "Отправлено"}

        if action == "broadcast":
            text = (data.get("text") or "").strip()
            target = data.get("target", "all")
            if not text:
                return JSONResponse({"status": "error", "message": "Текст рассылки пуст"}, status_code=400)
            if target == "all":
                stmt = select(User.telegram_id)
            elif target == "expiring":
                border = datetime.now(timezone.utc) + timedelta(days=3)
                stmt = select(Subscription.user_id).where(
                    Subscription.is_active.is_(True),
                    Subscription.end_date.is_not(None),
                    Subscription.end_date <= border,
                ).distinct()
            elif target == "tariff" and data.get("tariff_id"):
                stmt = select(Subscription.user_id).where(
                    Subscription.is_active.is_(True),
                    Subscription.tariff_id == int(data["tariff_id"]),
                ).distinct()
            else:
                stmt = select(Subscription.user_id).where(Subscription.is_active.is_(True)).distinct()
            user_ids = (await db.execute(stmt)).scalars().all()
            background_tasks.add_task(run_broadcast_task, bot, user_ids, text)
            return {"status": "ok", "message": f"Запущено на {len(user_ids)} пользователей"}

        return {"status": "ok", "message": "OK"}
    except Exception as e:
        logger.error(f"Error: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)




