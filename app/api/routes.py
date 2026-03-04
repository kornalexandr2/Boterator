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
from app.database.models import User, Tariff, Subscription, Payment
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
            # Simple rate limiting: 20 msg/sec is safe for TG
            # For massive broadcasts, more complex logic is needed
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
        # 1. Monthly revenue
        month_ago = datetime.now(timezone.utc) - timedelta(days=30)
        revenue_stmt = select(func.sum(Payment.amount)).where(
            Payment.status == "success",
            Payment.created_at >= month_ago
        )
        revenue_res = await db.execute(revenue_stmt)
        monthly_revenue = revenue_res.scalar() or 0

        # 2. New users (last 30 days)
        new_users_stmt = select(func.count(User.telegram_id)).where(User.created_at >= month_ago)
        new_users_res = await db.execute(new_users_stmt)
        new_users_count = new_users_res.scalar() or 0

        # 3. Active subscriptions
        active_subs_stmt = select(func.count(Subscription.id)).where(Subscription.is_active == True)
        active_subs_res = await db.execute(active_subs_stmt)
        active_count = active_subs_res.scalar() or 0

        # 4. Total users
        total_users_stmt = select(func.count(User.telegram_id))
        total_users_res = await db.execute(total_users_stmt)
        total_count = total_users_res.scalar() or 0

        return {
            "monthly_revenue": f"{monthly_revenue:,.0f} ₽",
            "new_users": f"+{new_users_count}",
            "active_subscriptions": active_count,
            "total_users": total_count,
            "churn_rate": "2.5%" # Mock for now
        }
    except Exception as e:
        logger.error(f"Failed to fetch stats: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@router.get("/tariffs")
async def list_tariffs(db: AsyncSession = Depends(get_db_session)):
    """Returns list of all tariffs."""
    stmt = select(Tariff).order_by(Tariff.id)
    result = await db.execute(stmt)
    tariffs = result.scalars().all()
    return tariffs

@router.post("/tariffs")
async def save_tariff(request: Request, db: AsyncSession = Depends(get_db_session)):
    """Creates or updates a tariff."""
    data = await request.json()
    tariff_id = data.get("id")
    
    try:
        if tariff_id:
            # Update existing
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
            # Create new
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
    """Deletes a tariff."""
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
    """Returns users with their active subscriptions."""
    stmt = select(User).order_by(User.created_at.desc()).limit(100)
    result = await db.execute(stmt)
    users = result.scalars().all()
    
    user_list = []
    for u in users:
        # Simple join-like logic for TWA list
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

from app.payments.base import MockProvider

@router.post("/action")
async def process_twa_action(request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db_session)):
    """Processes generic actions from TWA (buy, export, etc.)."""
    try:
        data = await request.json()
        action = data.get("action")
        bot = request.app.state.bot
        
        if not bot:
            return JSONResponse({"status": "error", "message": "Бот не запущен"}, status_code=500)

        # Basic ID retrieval (in real app validate tg.initData)
        # For simplicity, we assume data contains user_id or we get it from headers
        # Here we'll try to extract from initData if possible, but for mock let's use a placeholder or data
        user_id = data.get("user_id") 
        
        if action == "buy":
            tariff_id = data.get("tariff_id")
            email = data.get("email")
            
            # Fetch tariff
            t_stmt = select(Tariff).where(Tariff.id == tariff_id)
            t_res = await db.execute(t_stmt)
            tariff = t_res.scalar_one_or_none()
            
            if not tariff:
                return JSONResponse({"status": "error", "message": "Тариф не найден"}, status_code=404)

            # Create payment via MockProvider
            provider = MockProvider()
            pay_res = await provider.create_payment(
                amount=tariff.price,
                description=f"Оплата тарифа: {tariff.name}",
                metadata={"user_id": user_id, "tariff_id": tariff_id}
            )
            
            if pay_res.success:
                # Save payment to DB
                new_payment = Payment(
                    user_id=user_id,
                    amount=tariff.price,
                    provider="mock",
                    status="pending",
                    transaction_id=pay_res.transaction_id
                )
                db.add(new_payment)
                
                # If user has email, update it
                if email:
                    await db.execute(update(User).where(User.telegram_id == user_id).values(email=email))
                
                await db.commit()
                
                # Send link to user via bot
                await bot.send_message(
                    user_id, 
                    f"💳 <b>Оплата тарифа: {tariff.name}</b>\n\nСумма: {tariff.price} ₽\n\nДля оплаты перейдите по ссылке: {pay_res.payment_url}\n\n<i>(Это тестовая ссылка Mock-провайдера)</i>"
                )
                return JSONResponse({"status": "ok", "message": "Ссылка на оплату отправлена вам ботом."})
            else:
                return JSONResponse({"status": "error", "message": "Ошибка платежной системы"}, status_code=500)

        # Admin ID for export/broadcast
        admin_id = settings.bot.admin_ids[0] if settings.bot.admin_ids else None

        if action == "export_csv":
            stmt = select(User)
            res = await db.execute(stmt)
            users = res.scalars().all()
            
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Telegram ID", "Username", "First Name", "Last Name", "Email", "Phone", "Is Admin", "Created At"])
            for u in users:
                writer.writerow([u.telegram_id, u.username, u.first_name, u.last_name, u.email, u.phone, u.is_admin, u.created_at])
            
            csv_file = BufferedInputFile(output.getvalue().encode('utf-8'), filename=f"users_export_{datetime.now().strftime('%Y%m%d')}.csv")
            await bot.send_document(admin_id, csv_file, caption="Ваша выгрузка пользователей готова!")
            return JSONResponse({"status": "ok", "message": "Файл выгрузки отправлен ботом вам в ЛС."})

        if action == "broadcast":
            text = data.get("text")
            target = data.get("target", "all")
            if not text:
                return JSONResponse({"status": "error", "message": "Текст сообщения пуст"}, status_code=400)
            
            # Identify target user IDs
            if target == "all":
                stmt = select(User.telegram_id)
            elif target == "active":
                stmt = select(Subscription.user_id).where(Subscription.is_active == True).distinct()
            elif target == "expired":
                stmt = select(User.telegram_id).where(~User.telegram_id.in_(select(Subscription.user_id).where(Subscription.is_active == True)))
            
            res = await db.execute(stmt)
            user_ids = res.scalars().all()
            
            background_tasks.add_task(run_broadcast_task, bot, user_ids, text)
            return JSONResponse({"status": "ok", "message": f"Рассылка запущена для {len(user_ids)} пользователей."})
            
        return JSONResponse({"status": "ok", "message": f"Действие {action} обработано"})
    except Exception as e:
        logger.error(f"Error processing TWA action: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
