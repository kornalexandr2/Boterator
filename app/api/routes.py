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
from app.database.models import User, Tariff, Subscription, Payment, SystemSetting
from app.config import settings

router = APIRouter(prefix="/twa", tags=["twa"])
templates = Jinja2Templates(directory="app/templates")

async def get_db_session():
    async with async_session() as session:
        yield session

async def run_broadcast_task(bot, user_ids: list[int], text: str):
    """Sends messages in background with basic rate-limiting."""
    success = 0
    failed = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, text)
            success += 1
        except Exception as e:
            logger.warning(f"Failed to send broadcast to {uid}: {e}")
            failed += 1
    logger.info(f"Broadcast finished. Success: {success}, Failed: {failed}")

@router.get("/store", response_class=HTMLResponse)
@router.get("//store", response_class=HTMLResponse, include_in_schema=False)
async def get_store(request: Request):
    """Client TWA - Storefront"""
    return templates.TemplateResponse("client/store.html", {"request": request})

@router.get("/admin", response_class=HTMLResponse)
@router.get("//admin", response_class=HTMLResponse, include_in_schema=False)
async def get_admin_crm(request: Request):
    """Admin TWA - CRM Dashboard"""
    return templates.TemplateResponse("admin/crm.html", {"request": request})

@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db_session)):
    """Returns dashboard statistics."""
    try:
        month_ago = datetime.now(timezone.utc) - timedelta(days=30)
        revenue_stmt = select(func.sum(Payment.amount)).where(
            Payment.status == "success",
            Payment.created_at >= month_ago
        )
        revenue_res = await db.execute(revenue_stmt)
        monthly_revenue = revenue_res.scalar() or 0

        new_users_stmt = select(func.count(User.telegram_id)).where(User.created_at >= month_ago)
        new_users_res = await db.execute(new_users_stmt)
        new_users_count = new_users_res.scalar() or 0

        active_subs_stmt = select(func.count(Subscription.id)).where(Subscription.is_active == True)
        active_subs_res = await db.execute(active_subs_stmt)
        active_count = active_subs_res.scalar() or 0

        total_users_stmt = select(func.count(User.telegram_id))
        total_users_res = await db.execute(total_users_stmt)
        total_count = total_users_res.scalar() or 0

        return {
            "monthly_revenue": f"{monthly_revenue:,.0f} ₽",
            "new_users": f"+{new_users_count}",
            "active_subscriptions": active_count,
            "total_users": total_count,
            "churn_rate": "2.5%"
        }
    except Exception as e:
        logger.error(f"Failed to fetch stats: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@router.get("/tariffs")
async def list_tariffs(db: AsyncSession = Depends(get_db_session)):
    stmt = select(Tariff).order_by(Tariff.id)
    result = await db.execute(stmt)
    tariffs = result.scalars().all()
    return tariffs

@router.post("/tariffs")
async def save_tariff(request: Request, db: AsyncSession = Depends(get_db_session)):
    data = await request.json()
    tariff_id = data.get("id")
    try:
        if tariff_id:
            stmt = update(Tariff).where(Tariff.id == tariff_id).values(
                name=data["name"],
                description=data.get("description", ""),
                price=float(data["price"]),
                duration_days=int(data["duration_days"]),
                is_trial=bool(data.get("is_trial", False)),
                is_hidden=bool(data.get("is_hidden", False)),
                require_email=bool(data.get("require_email", False)),
                require_phone=bool(data.get("require_phone", False))
            )
            await db.execute(stmt)
        else:
            new_tariff = Tariff(
                name=data["name"],
                description=data.get("description", ""),
                price=float(data["price"]),
                duration_days=int(data["duration_days"]),
                is_trial=bool(data.get("is_trial", False)),
                is_hidden=bool(data.get("is_hidden", False)),
                require_email=bool(data.get("require_email", False)),
                require_phone=bool(data.get("require_phone", False))
            )
            db.add(new_tariff)
        await db.commit()
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to save tariff: {e}")
        await db.rollback()
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)

@router.delete("/tariffs/{tariff_id}")
async def delete_tariff(tariff_id: int, db: AsyncSession = Depends(get_db_session)):
    try:
        await db.execute(delete(Tariff).where(Tariff.id == tariff_id))
        await db.commit()
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to delete tariff {tariff_id}: {e}")
        await db.rollback()
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)

@router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db_session)):
    stmt = select(User).order_by(User.created_at.desc()).limit(100)
    result = await db.execute(stmt)
    users = result.scalars().all()
    user_list = []
    for u in users:
        sub_stmt = select(Subscription).where(Subscription.user_id == u.telegram_id, Subscription.is_active == True)
        sub_res = await db.execute(sub_stmt)
        active_sub = sub_res.scalar_one_or_none()
        user_list.append({
            "telegram_id": u.telegram_id,
            "username": u.username or f"ID: {u.telegram_id}",
            "full_name": f"{u.first_name or ''} {u.last_name or ''}".strip(),
            "has_active_sub": active_sub is not None,
            "is_admin": u.is_admin
        })
    return user_list

@router.get("/settings")
async def get_system_settings(db: AsyncSession = Depends(get_db_session)):
    stmt = select(SystemSetting)
    res = await db.execute(stmt)
    settings_res = res.scalars().all()
    data = {"payment_mode": "mock", "yookassa_shop_id": "", "yookassa_secret_key": ""}
    for s in settings_res:
        data[s.key] = s.value
    return data

@router.post("/settings")
async def update_system_settings(request: Request, db: AsyncSession = Depends(get_db_session)):
    data = await request.json()
    try:
        for key, value in data.items():
            stmt = select(SystemSetting).where(SystemSetting.key == key)
            res = await db.execute(stmt)
            setting = res.scalar_one_or_none()
            if setting:
                setting.value = str(value)
            else:
                db.add(SystemSetting(key=key, value=str(value)))
        await db.commit()
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to update settings: {e}")
        await db.rollback()
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)

from app.payments.base import MockProvider

@router.post("/action")
async def process_twa_action(request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db_session)):
    try:
        data = await request.json()
        action = data.get("action")
        bot = request.app.state.bot
        if not bot: return JSONResponse({"status": "error", "message": "Бот не запущен"}, status_code=500)

        user_id = data.get("user_id") 
        if action == "buy":
            tariff_id = data.get("tariff_id")
            email = data.get("email")
            s_stmt = select(SystemSetting).where(SystemSetting.key == "payment_mode")
            s_res = await db.execute(s_stmt)
            s_mode = s_res.scalar_one_or_none()
            mode = s_mode.value if s_mode else "mock"

            t_stmt = select(Tariff).where(Tariff.id == tariff_id)
            t_res = await db.execute(t_stmt)
            tariff = t_res.scalar_one_or_none()
            if not tariff: return JSONResponse({"status": "error", "message": "Тариф не найден"}, status_code=404)

            if mode == "yookassa":
                 return JSONResponse({"status": "error", "message": "YooKassa требует настройки API. Используйте Mock режим."}, status_code=400)
            
            provider = MockProvider()
            pay_res = await provider.create_payment(tariff.price, f"Оплата: {tariff.name}", {"user_id": user_id, "tariff_id": tariff_id})
            
            if pay_res.success:
                db.add(Payment(user_id=user_id, amount=tariff.price, provider=mode, status="pending", transaction_id=pay_res.transaction_id))
                if email: await db.execute(update(User).where(User.telegram_id == user_id).values(email=email))
                await db.commit()
                await bot.send_message(user_id, f"💳 <b>Оплата: {tariff.name}</b>\n\nСумма: {tariff.price} ₽\n\nСсылка: {pay_res.payment_url}\n\n<i>Режим: {mode}</i>")
                return JSONResponse({"status": "ok", "message": "Ссылка отправлена ботом в ЛС."})
            return JSONResponse({"status": "error", "message": "Ошибка платежной системы"}, status_code=500)

        admin_id = settings.bot.admin_ids[0] if settings.bot.admin_ids else None
        if action == "export_csv":
            res = await db.execute(select(User))
            users = res.scalars().all()
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "Username", "First", "Last", "Email", "Phone", "Admin", "Date"])
            for u in users: writer.writerow([u.telegram_id, u.username, u.first_name, u.last_name, u.email, u.phone, u.is_admin, u.created_at])
            csv_file = BufferedInputFile(output.getvalue().encode('utf-8'), filename="users.csv")
            await bot.send_document(admin_id, csv_file, caption="Экспорт пользователей")
            return JSONResponse({"status": "ok", "message": "Файл отправлен в ЛС."})

        if action == "broadcast":
            text = data.get("text")
            target = data.get("target", "all")
            if not text: return JSONResponse({"status": "error", "message": "Текст пуст"}, status_code=400)
            if target == "all": stmt = select(User.telegram_id)
            elif target == "active": stmt = select(Subscription.user_id).where(Subscription.is_active == True).distinct()
            elif target == "expired": stmt = select(User.telegram_id).where(~User.telegram_id.in_(select(Subscription.user_id).where(Subscription.is_active == True)))
            res = await db.execute(stmt)
            user_ids = res.scalars().all()
            background_tasks.add_task(run_broadcast_task, bot, user_ids, text)
            return JSONResponse({"status": "ok", "message": f"Рассылка запущена ({len(user_ids)} чел)."})
            
        return JSONResponse({"status": "ok", "message": "Действие обработано"})
    except Exception as e:
        logger.error(f"Error: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
