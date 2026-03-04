from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, update
from datetime import datetime, timedelta, timezone
import json
import csv
import io

from app.database.session import async_session
from app.database.models import User, Tariff, Subscription, Payment
from app.config import settings

router = APIRouter(prefix="/twa", tags=["twa"])
templates = Jinja2Templates(directory="app/templates")

async def get_db_session():
    async with async_session() as session:
        yield session

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

@router.post("/action")
async def process_twa_action(request: Request, db: AsyncSession = Depends(get_db_session)):
    """Processes generic actions from TWA (export, broadcast, etc.)."""
    try:
        data = await request.json()
        action = data.get("action")
        
        logger.info(f"TWA Action received: {action}")
        
        if action == "export_csv":
            # Just a mock confirmation for now, file generation is complex via fetch
            return JSONResponse({"status": "ok", "message": "Выгрузка CSV запущена. Бот пришлет файл."})
            
        return JSONResponse({"status": "ok", "message": f"Действие {action} обработано"})
    except Exception as e:
        logger.error(f"Error processing TWA action: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
