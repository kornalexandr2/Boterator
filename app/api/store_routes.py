import json
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import (
    authenticate_request,
    chat_to_dict,
    finalize_successful_payment,
    get_db_session,
    get_tariff_resource_map,
    resolve_runtime_settings,
    tariff_to_dict,
)
from app.database.models import ManagedChat, Payment, Tariff, TariffResource
from app.payments.base import build_payment_provider

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/store", response_class=HTMLResponse)
async def get_store(request: Request):
    return templates.TemplateResponse("client/store.html", {"request": request})


@router.get("/me")
async def get_me(request: Request, db: AsyncSession | None = Depends(get_db_session)):
    _, user, role, error = await authenticate_request(request, db)
    if error:
        return error
    runtime_settings = await resolve_runtime_settings(db)
    return {
        "status": "ok",
        "user": {
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone": user.phone,
            "role": role,
            "is_eternal": bool(user.is_eternal),
        },
        "permissions": {
            "is_staff": role in {"super_admin", "admin", "moderator"},
            "can_manage_roles": role == "super_admin",
            "can_manage_settings": role in {"super_admin", "admin"},
        },
        "offer_url": runtime_settings.get("offer_url", ""),
        "privacy_url": runtime_settings.get("privacy_url", ""),
    }


@router.get("/managed-chats")
async def list_managed_chats(request: Request, db: AsyncSession | None = Depends(get_db_session)):
    _, _, role, error = await authenticate_request(request, db)
    if error:
        return error
    chats = (await db.execute(select(ManagedChat).order_by(ManagedChat.title.asc()))).scalars().all()
    links = (await db.execute(select(TariffResource.tariff_id, TariffResource.chat_id))).all()
    assigned_map: dict[int, set[int]] = {}
    for tariff_id, chat_id in links:
        assigned_map.setdefault(int(chat_id), set()).add(int(tariff_id))
    result = []
    for chat in chats:
        if role == "user" and not chat.is_active:
            continue
        result.append(chat_to_dict(chat, assigned_map.get(int(chat.chat_id), set())))
    return result


@router.get("/tariffs")
async def list_tariffs(request: Request, db: AsyncSession | None = Depends(get_db_session)):
    _, _, role, error = await authenticate_request(request, db)
    if error:
        return error
    tariffs = (await db.execute(select(Tariff).order_by(Tariff.id))).scalars().all()
    resource_map = await get_tariff_resource_map(db)
    result = []
    for tariff in tariffs:
        if role == "user" and tariff.is_hidden:
            continue
        result.append(tariff_to_dict(tariff, resource_map.get(int(tariff.id), [])))
    return result


@router.post("/payments/confirm")
async def confirm_payment(request: Request, db: AsyncSession | None = Depends(get_db_session)):
    payload = await request.json()
    _, user, role, error = await authenticate_request(request, db, payload)
    if error:
        return error

    transaction_id = (payload.get("transaction_id") or "").strip()
    if not transaction_id:
        return JSONResponse({"status": "error", "message": "transaction_id обязателен"}, status_code=400)

    payment = (await db.execute(select(Payment).where(Payment.transaction_id == transaction_id))).scalar_one_or_none()
    if not payment:
        return JSONResponse({"status": "error", "message": "Платеж не найден"}, status_code=404)
    if role == "user" and payment.user_id != user.telegram_id:
        return JSONResponse({"status": "error", "message": "Недостаточно прав"}, status_code=403)
    if payment.status == "success":
        return {"status": "ok", "payment_status": "success", "message": "Платеж уже подтвержден"}
    if payment.status == "refunded":
        return {"status": "ok", "payment_status": "refunded", "message": "Платеж уже возвращен"}

    runtime_settings = await resolve_runtime_settings(db)
    provider = build_payment_provider(
        payment.provider,
        yoomoney_receiver=runtime_settings.get("yoomoney_receiver", ""),
        yookassa_shop_id=runtime_settings.get("yookassa_shop_id", ""),
        yookassa_secret_key=runtime_settings.get("yookassa_secret_key", ""),
        sberbank_username=runtime_settings.get("sberbank_username", ""),
        sberbank_password=runtime_settings.get("sberbank_password", ""),
    )

    if payment.provider == "yoomoney":
        return {"status": "ok", "payment_status": payment.status, "message": "Платеж ожидает ручного подтверждения администратором"}

    status = await provider.check_status(transaction_id)
    if status != "success":
        payment.status = "failed" if status == "failed" else "pending"
        await db.commit()
        return {"status": "ok", "payment_status": payment.status, "message": "Платеж пока не подтвержден"}

    details = await provider.get_payment_details(transaction_id)
    payment.raw_payload = json.dumps(details, ensure_ascii=False, default=str)
    payment.recurring_token = details.get("recurring_token") or payment.recurring_token
    return await finalize_successful_payment(db, request, payment)


