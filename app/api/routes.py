from fastapi import APIRouter, Request, HTTPException, Depends, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, update
from datetime import datetime, timedelta, timezone
import json
import csv
import io
from aiogram.types import BufferedInputFile

from app.database.session import async_session
from app.database.models import User, Tariff, Subscription, Payment, SystemSetting, ManagedChat
from app.config import settings

router = APIRouter(prefix="/twa", tags=["twa"])
templates = Jinja2Templates(directory="app/templates")

async def get_db_session():
    async with async_session() as session:
        yield session

async def run_broadcast_task(bot, user_ids: list[int], text: str):
    success = 0; failed = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, text)
            success += 1
        except Exception: failed += 1
    logger.info(f"Broadcast: {success} ok, {failed} failed")

@router.get("/store", response_class=HTMLResponse)
async def get_store(request: Request):
    return templates.TemplateResponse("client/store.html", {"request": request})

@router.get("/admin", response_class=HTMLResponse)
async def get_admin_crm(request: Request):
    return templates.TemplateResponse("admin/crm.html", {"request": request})

@router.get("/managed-chats")
async def list_managed_chats(db: AsyncSession = Depends(get_db_session)):
    stmt = select(ManagedChat).where(ManagedChat.is_active == True)
    res = await db.execute(stmt)
    return res.scalars().all()

@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db_session)):
    try:
        month_ago = datetime.now(timezone.utc) - timedelta(days=30)
        rev = (await db.execute(select(func.sum(Payment.amount)).where(Payment.status == "success", Payment.created_at >= month_ago))).scalar() or 0
        new_u = (await db.execute(select(func.count(User.telegram_id)).where(User.created_at >= month_ago))).scalar() or 0
        act_s = (await db.execute(select(func.count(Subscription.id)).where(Subscription.is_active == True))).scalar() or 0
        tot_u = (await db.execute(select(func.count(User.telegram_id)))).scalar() or 0
        return {"monthly_revenue": f"{rev:,.0f} ₽", "new_users": f"+{new_u}", "active_subscriptions": act_s, "total_users": tot_u}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@router.get("/tariffs")
async def list_tariffs(db: AsyncSession = Depends(get_db_session)):
    return (await db.execute(select(Tariff).order_by(Tariff.id))).scalars().all()

@router.post("/tariffs")
async def save_tariff(request: Request, db: AsyncSession = Depends(get_db_session)):
    data = await request.json(); tid = data.get("id")
    try:
        if tid:
            await db.execute(update(Tariff).where(Tariff.id == tid).values(name=data["name"], description=data.get("description", ""), price=float(data["price"]), duration_days=int(data["duration_days"]), is_trial=bool(data.get("is_trial", False)), is_hidden=bool(data.get("is_hidden", False)), require_email=bool(data.get("require_email", False)), require_phone=bool(data.get("require_phone", False))))
        else:
            db.add(Tariff(name=data["name"], description=data.get("description", ""), price=float(data["price"]), duration_days=int(data["duration_days"]), is_trial=bool(data.get("is_trial", False)), is_hidden=bool(data.get("is_hidden", False)), require_email=bool(data.get("require_email", False)), require_phone=bool(data.get("require_phone", False))))
        await db.commit(); return {"status": "ok"}
    except Exception as e:
        await db.rollback(); return JSONResponse({"status": "error", "message": str(e)}, status_code=400)

@router.delete("/tariffs/{tariff_id}")
async def delete_tariff(tariff_id: int, db: AsyncSession = Depends(get_db_session)):
    await db.execute(delete(Tariff).where(Tariff.id == tariff_id))
    await db.commit(); return {"status": "ok"}

@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db_session)):
    users = (await db.execute(select(User).order_by(User.created_at.desc()).limit(100))).scalars().all()
    res_list = []
    for u in users:
        active = (await db.execute(select(Subscription).where(Subscription.user_id == u.telegram_id, Subscription.is_active == True))).scalar_one_or_none()
        res_list.append({"telegram_id": u.telegram_id, "username": u.username or f"ID: {u.telegram_id}", "full_name": f"{u.first_name or ''} {u.last_name or ''}".strip(), "has_active_sub": active is not None, "is_admin": u.telegram_id in settings.bot.admin_ids or u.is_admin, "is_super": u.telegram_id in settings.bot.admin_ids})
    return res_list

@router.get("/settings")
async def get_system_settings(db: AsyncSession = Depends(get_db_session)):
    res = (await db.execute(select(SystemSetting))).scalars().all()
    data = {"payment_mode": "mock", "yoomoney_receiver": ""}
    for s in res: data[s.key] = s.value
    return data

@router.post("/settings")
async def update_system_settings(request: Request, db: AsyncSession = Depends(get_db_session)):
    data = await request.json()
    for k, v in data.items():
        s = (await db.execute(select(SystemSetting).where(SystemSetting.key == k))).scalar_one_or_none()
        if s: s.value = str(v)
        else: db.add(SystemSetting(key=k, value=str(v)))
    await db.commit(); return {"status": "ok"}

from app.payments.base import MockProvider, YooMoneyProvider
from app.bot.handlers.admin_events import check_bot_permissions

@router.post("/action")
async def process_twa_action(request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db_session)):
    try:
        data = await request.json(); action = data.get("action"); bot = request.app.state.bot
        if not bot: return JSONResponse({"status": "error", "message": "Бот не запущен"}, status_code=500)
        
        user_id = data.get("user_id")
        
        if action == "sync_resources":
            chats = (await db.execute(select(ManagedChat))).scalars().all()
            sync_count = 0
            for c in chats:
                try:
                    member = await bot.get_chat_member(c.chat_id, (await bot.get_me()).id)
                    from aiogram.enums import ChatMemberStatus
                    if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                        from app.bot.handlers.admin_events import check_bot_permissions
                        missing = check_bot_permissions(member)
                        c.permissions_ok = len(missing) == 0
                        c.missing_permissions = ", ".join(missing) if missing else None
                        c.is_active = True
                        sync_count += 1
                    else:
                        c.is_active = False
                except Exception: c.is_active = False
            await db.commit()
            return JSONResponse({"status": "ok", "message": f"Обновлено ресурсов: {sync_count}. Если группы нет в списке — удалите и добавьте бота заново."})

        if action == "buy":
            tid = data.get("tariff_id")
            tariff = (await db.execute(select(Tariff).where(Tariff.id == tid))).scalar_one_or_none()
            if not tariff: return JSONResponse({"status": "error", "message": "Тариф не найден"}, status_code=404)
            if tariff.is_trial:
                if (await db.execute(select(Subscription).join(Tariff).where(Subscription.user_id == user_id, Tariff.is_trial == True))).scalar_one_or_none():
                    return JSONResponse({"status": "error", "message": "Триал уже был"}, status_code=400)
                end = datetime.now(timezone.utc) + timedelta(days=tariff.duration_days)
                db.add(Subscription(user_id=user_id, tariff_id=tariff.id, start_date=datetime.now(timezone.utc), end_date=end, is_active=True))
                await db.commit()
                links = "\n".join([f"🔹 <a href='{c.invite_link}'>{c.title}</a>" for c in (await db.execute(select(ManagedChat).where(ManagedChat.is_active == True))).scalars().all() if c.invite_link])
                await bot.send_message(user_id, f"✅ <b>Триал активирован!</b>\nДо: {end.strftime('%d.%m.%Y')}\n\n<b>Ссылки:</b>\n{links}")
                return JSONResponse({"status": "ok", "message": "Активировано!"})
            s_dict = {s.key: s.value for s in (await db.execute(select(SystemSetting))).scalars().all()}
            mode = s_dict.get("payment_mode", "mock")
            provider = YooMoneyProvider(receiver=s_dict.get("yoomoney_receiver", "")) if mode == "yoomoney" else MockProvider()
            pay_res = await provider.create_payment(tariff.price, f"Оплата: {tariff.name}", {"user_id": user_id, "tariff_id": tid})
            if pay_res.success:
                db.add(Payment(user_id=user_id, amount=tariff.price, provider=mode, status="pending", transaction_id=pay_res.transaction_id))
                await db.commit()
                await bot.send_message(user_id, f"💳 <b>Оплата: {tariff.name}</b>\nСумма: {tariff.price} ₽\nСсылка: {pay_res.payment_url}")
                return JSONResponse({"status": "ok", "message": "Ссылка в ЛС!"})
            return JSONResponse({"status": "error", "message": "Ошибка оплаты"}, status_code=500)

        if action == "promote_admin":
            await db.execute(update(User).where(User.telegram_id == data.get("target_id")).values(is_admin=True))
            await db.commit(); return {"status": "ok", "message": "Назначен админ"}
        if action == "demote_admin":
            if data.get("target_id") in settings.bot.admin_ids: return JSONResponse({"status": "error", "message": "Нельзя снять Super Admin"}, status_code=400)
            await db.execute(update(User).where(User.telegram_id == data.get("target_id")).values(is_admin=False))
            await db.commit(); return {"status": "ok", "message": "Админ снят"}
        if action == "kick_user":
            await db.execute(update(Subscription).where(Subscription.user_id == data.get("target_id")).values(is_active=False))
            await db.commit()
            for c in (await db.execute(select(ManagedChat).where(ManagedChat.is_active == True))).scalars().all():
                try: await bot.ban_chat_member(c.chat_id, data.get("target_id")); await bot.unban_chat_member(c.chat_id, data.get("target_id"))
                except Exception: pass
            try: await bot.send_message(data.get("target_id"), f"🔴 <b>Вы исключены.</b>\nПричина: {data.get('reason')}")
            except Exception: pass
            return {"status": "ok", "message": "Исключен"}
        if action == "export_csv":
            users = (await db.execute(select(User))).scalars().all()
            output = io.StringIO(); writer = csv.writer(output); writer.writerow(["ID", "User", "Email", "Admin", "Date"])
            for u in users: writer.writerow([u.telegram_id, u.username, u.email, u.is_admin, u.created_at])
            await bot.send_document(settings.bot.admin_ids[0], BufferedInputFile(output.getvalue().encode('utf-8'), filename="users.csv"), caption="Экспорт")
            return {"status": "ok", "message": "Отправлено"}
        if action == "broadcast":
            text = data.get("text"); target = data.get("target", "all")
            stmt = select(User.telegram_id) if target == "all" else select(Subscription.user_id).where(Subscription.is_active == True).distinct()
            background_tasks.add_task(run_broadcast_task, bot, (await db.execute(stmt)).scalars().all(), text)
            return {"status": "ok", "message": "Запущено"}
        return {"status": "ok", "message": "OK"}
    except Exception as e:
        logger.error(f"Error: {e}"); return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
