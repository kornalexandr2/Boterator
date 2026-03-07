import json
from datetime import timedelta

from aiogram.types import BufferedInputFile
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.api.common import (
    activate_user_access,
    authenticate_request,
    bad_request_response,
    build_users_csv,
    chat_to_dict,
    ensure_staff,
    finalize_successful_payment,
    get_db_session,
    get_tariff_resource_map,
    resolve_runtime_settings,
    run_broadcast_task,
    serialize_user_list,
    tariff_to_dict,
    utcnow,
    update_user_contacts,
)
from app.bot.handlers.admin_events import check_bot_permissions
from app.database.models import ManagedChat, Payment, Subscription, SystemSetting, Tariff, TariffResource, User
from app.payments.base import build_payment_provider
from app.services.access import revoke_user_from_inaccessible_chats

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/admin", response_class=HTMLResponse)
async def get_admin_crm(request: Request):
    return templates.TemplateResponse("admin/crm.html", {"request": request})


@router.get("/stats")
async def get_stats(request: Request, db: AsyncSession | None = Depends(get_db_session)):
    _, _, role, error = await authenticate_request(request, db)
    if error:
        return error
    access_error = await ensure_staff(role)
    if access_error:
        return access_error
    month_ago = utcnow() - timedelta(days=30)
    revenue = (await db.execute(select(func.sum(Payment.amount)).where(Payment.status == "success", Payment.created_at >= month_ago))).scalar() or 0
    new_users = (await db.execute(select(func.count(User.telegram_id)).where(User.created_at >= month_ago))).scalar() or 0
    active_subscriptions = (await db.execute(select(func.count(Subscription.id)).where(Subscription.is_active.is_(True)))).scalar() or 0
    total_users = (await db.execute(select(func.count(User.telegram_id)))).scalar() or 0
    churn_base = (await db.execute(select(func.count(Subscription.id)).where(Subscription.start_date < month_ago))).scalar() or 0
    churn_lost = (
        await db.execute(
            select(func.count(Subscription.id)).where(
                Subscription.end_date.is_not(None),
                Subscription.end_date >= month_ago,
                Subscription.end_date < utcnow(),
            )
        )
    ).scalar() or 0
    churn_rate = round((churn_lost / churn_base) * 100, 1) if churn_base else 0.0
    return {
        "monthly_revenue": f"{revenue:,.0f} RUB",
        "new_users": f"+{new_users}",
        "active_subscriptions": active_subscriptions,
        "total_users": total_users,
        "churn_rate": f"{churn_rate}%",
    }


@router.post("/tariffs")
async def save_tariff(request: Request, db: AsyncSession | None = Depends(get_db_session)):
    payload = await request.json()
    _, _, role, error = await authenticate_request(request, db, payload)
    if error:
        return error
    access_error = await ensure_staff(role, admin_only=True)
    if access_error:
        return access_error
    try:
        tariff_id = int(payload["id"]) if payload.get("id") else None
        values = {
            "name": str(payload["name"]).strip(),
            "description": str(payload.get("description", "")).strip(),
            "price": float(payload["price"]),
            "duration_days": int(payload["duration_days"]),
            "is_trial": bool(payload.get("is_trial", False)),
            "is_hidden": bool(payload.get("is_hidden", False)),
            "require_email": bool(payload.get("require_email", False)),
            "require_phone": bool(payload.get("require_phone", False)),
        }
        resource_ids = sorted({int(chat_id) for chat_id in payload.get("resource_ids", [])})
        if tariff_id:
            tariff = (await db.execute(select(Tariff).where(Tariff.id == tariff_id))).scalar_one_or_none()
            if not tariff:
                return bad_request_response("Тариф не найден")
            for key, value in values.items():
                setattr(tariff, key, value)
        else:
            tariff = Tariff(**values)
            db.add(tariff)
            await db.flush()
        await db.execute(delete(TariffResource).where(TariffResource.tariff_id == tariff.id))
        for chat_id in resource_ids:
            db.add(TariffResource(tariff_id=tariff.id, chat_id=chat_id))
        await db.commit()
        return {"status": "ok", "tariff_id": tariff.id}
    except Exception as exc:
        await db.rollback()
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)


@router.delete("/tariffs/{tariff_id}")
async def delete_tariff(tariff_id: int, request: Request, db: AsyncSession | None = Depends(get_db_session)):
    _, _, role, error = await authenticate_request(request, db)
    if error:
        return error
    access_error = await ensure_staff(role, admin_only=True)
    if access_error:
        return access_error
    await db.execute(delete(Tariff).where(Tariff.id == tariff_id))
    await db.commit()
    return {"status": "ok"}


@router.get("/users")
async def list_users(request: Request, db: AsyncSession | None = Depends(get_db_session)):
    _, _, role, error = await authenticate_request(request, db)
    if error:
        return error
    access_error = await ensure_staff(role)
    if access_error:
        return access_error
    return await serialize_user_list(db)


@router.get("/payments")
async def list_payments(request: Request, db: AsyncSession | None = Depends(get_db_session)):
    _, _, role, error = await authenticate_request(request, db)
    if error:
        return error
    access_error = await ensure_staff(role)
    if access_error:
        return access_error
    rows = (
        await db.execute(
            select(Payment, User, Tariff)
            .outerjoin(User, User.telegram_id == Payment.user_id)
            .outerjoin(Tariff, Tariff.id == Payment.tariff_id)
            .order_by(Payment.created_at.desc())
            .limit(200)
        )
    ).all()
    result = []
    for payment, user, tariff in rows:
        result.append(
            {
                "id": payment.id,
                "transaction_id": payment.transaction_id,
                "status": payment.status,
                "provider": payment.provider,
                "amount": payment.amount,
                "user_id": payment.user_id,
                "username": user.username if user else None,
                "tariff_id": payment.tariff_id,
                "tariff_name": tariff.name if tariff else None,
                "contact_email": payment.contact_email,
                "contact_phone": payment.contact_phone,
                "refund_id": payment.refund_id,
                "created_at": payment.created_at.isoformat() if payment.created_at else None,
            }
        )
    return result


@router.get("/settings")
async def get_system_settings(request: Request, db: AsyncSession | None = Depends(get_db_session)):
    _, _, role, error = await authenticate_request(request, db)
    if error:
        return error
    access_error = await ensure_staff(role)
    if access_error:
        return access_error
    return await resolve_runtime_settings(db)


@router.post("/settings")
async def update_system_settings(request: Request, db: AsyncSession | None = Depends(get_db_session)):
    payload = await request.json()
    _, _, role, error = await authenticate_request(request, db, payload)
    if error:
        return error
    access_error = await ensure_staff(role, admin_only=True)
    if access_error:
        return access_error
    allowed_keys = {
        "payment_mode",
        "yoomoney_receiver",
        "yookassa_shop_id",
        "yookassa_secret_key",
        "sberbank_username",
        "sberbank_password",
        "offer_url",
        "privacy_url",
        "grace_period_days",
    }
    for key, value in payload.items():
        if key not in allowed_keys:
            continue
        row = (await db.execute(select(SystemSetting).where(SystemSetting.key == key))).scalar_one_or_none()
        if row:
            row.value = str(value)
        else:
            db.add(SystemSetting(key=key, value=str(value)))
    await db.commit()
    return {"status": "ok"}

@router.post("/payments/{payment_id}/refund")
async def refund_payment(payment_id: int, request: Request, db: AsyncSession | None = Depends(get_db_session)):
    payload = await request.json()
    _, _, role, error = await authenticate_request(request, db, payload)
    if error:
        return error
    access_error = await ensure_staff(role, admin_only=True)
    if access_error:
        return access_error

    payment = (await db.execute(select(Payment).where(Payment.id == payment_id))).scalar_one_or_none()
    if not payment:
        return JSONResponse({"status": "error", "message": "Платеж не найден"}, status_code=404)
    if payment.status != "success":
        return bad_request_response("Возврат доступен только для успешного платежа")

    runtime_settings = await resolve_runtime_settings(db)
    provider = build_payment_provider(
        payment.provider,
        yoomoney_receiver=runtime_settings.get("yoomoney_receiver", ""),
        yookassa_shop_id=runtime_settings.get("yookassa_shop_id", ""),
        yookassa_secret_key=runtime_settings.get("yookassa_secret_key", ""),
        sberbank_username=runtime_settings.get("sberbank_username", ""),
        sberbank_password=runtime_settings.get("sberbank_password", ""),
    )
    refund = await provider.refund_payment(payment.transaction_id or "", payment.amount)
    if not refund.success:
        return JSONResponse({"status": "error", "message": refund.error_message or "Ошибка возврата"}, status_code=400)

    payment.status = "refunded"
    payment.refund_id = refund.refund_id
    payment.refunded_at = utcnow()
    await db.execute(
        update(Subscription)
        .where(Subscription.user_id == payment.user_id, Subscription.tariff_id == payment.tariff_id, Subscription.is_active.is_(True))
        .values(is_active=False, in_grace_period=False, grace_end_date=None)
    )
    await db.commit()
    if request.app.state.bot:
        await revoke_user_from_inaccessible_chats(request.app.state.bot, db, payment.user_id)
    return {"status": "ok", "message": "Возврат выполнен"}


@router.post("/action")
async def process_twa_action(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession | None = Depends(get_db_session),
):
    payload = await request.json()
    _, user, role, error = await authenticate_request(request, db, payload)
    if error:
        return error
    bot = request.app.state.bot
    action = payload.get("action")

    if not bot:
        return JSONResponse({"status": "error", "message": "Бот не запущен"}, status_code=500)

    try:
        if action == "buy":
            tariff_id = int(payload.get("tariff_id") or 0)
            tariff = (await db.execute(select(Tariff).where(Tariff.id == tariff_id))).scalar_one_or_none()
            if not tariff:
                return JSONResponse({"status": "error", "message": "Тариф не найден"}, status_code=404)
            if tariff.is_hidden and role == "user":
                return JSONResponse({"status": "error", "message": "Скрытый тариф недоступен"}, status_code=403)
            email = (payload.get("email") or "").strip() or None
            phone = (payload.get("phone") or "").strip() or None
            if tariff.require_email and not email:
                return bad_request_response("Email обязателен для этого тарифа")
            if tariff.require_phone and not phone:
                return bad_request_response("Телефон обязателен для этого тарифа")
            await update_user_contacts(user, email, phone)
            if tariff.is_trial:
                trial_exists = (
                    await db.execute(
                        select(Subscription)
                        .join(Tariff, Tariff.id == Subscription.tariff_id)
                        .where(Subscription.user_id == user.telegram_id, Tariff.is_trial.is_(True))
                    )
                ).scalar_one_or_none()
                if trial_exists:
                    return bad_request_response("Триал уже был выдан")
                await db.commit()
                await activate_user_access(db, bot, user=user, tariff=tariff, paid=False)
                return {"status": "ok", "message": "Триал активирован"}
            runtime_settings = await resolve_runtime_settings(db)
            mode = (runtime_settings.get("payment_mode") or "mock").strip().lower()
            provider = build_payment_provider(
                mode,
                yoomoney_receiver=runtime_settings.get("yoomoney_receiver", ""),
                yookassa_shop_id=runtime_settings.get("yookassa_shop_id", ""),
                yookassa_secret_key=runtime_settings.get("yookassa_secret_key", ""),
                sberbank_username=runtime_settings.get("sberbank_username", ""),
                sberbank_password=runtime_settings.get("sberbank_password", ""),
            )
            payment_result = await provider.create_payment(
                tariff.price,
                f"Оплата тарифа {tariff.name}",
                {"user_id": user.telegram_id, "tariff_id": tariff.id},
                return_url=f"{request.base_url}twa/store",
                save_payment_method=mode in {"mock", "yookassa", "sberbank"},
            )
            if not payment_result.success:
                return JSONResponse({"status": "error", "message": payment_result.error_message or "Ошибка оплаты"}, status_code=400)
            payment = Payment(
                user_id=user.telegram_id,
                tariff_id=tariff.id,
                amount=tariff.price,
                provider=mode,
                status="success" if mode == "mock" else "pending",
                transaction_id=payment_result.transaction_id,
                contact_email=email,
                contact_phone=phone,
                recurring_token=payment_result.recurring_token,
                raw_payload=json.dumps(payment_result.raw, ensure_ascii=False, default=str),
            )
            db.add(payment)
            await db.commit()
            await db.refresh(payment)
            if mode == "mock":
                return await finalize_successful_payment(db, request, payment)
            return {
                "status": "ok",
                "message": "Заявка создана. Для YooMoney подтверждение выполняет администратор." if mode == "yoomoney" else "Платеж создан",
                "transaction_id": payment.transaction_id,
                "provider": payment.provider,
                "payment_url": payment_result.payment_url,
            }

        if action == "add_resource":
            access_error = await ensure_staff(role, admin_only=True)
            if access_error:
                return access_error
            chat_id = int(payload.get("chat_id") or 0)
            if not chat_id:
                return bad_request_response("chat_id обязателен")
            chat = await bot.get_chat(chat_id)
            member = await bot.get_chat_member(chat_id, (await bot.get_me()).id)
            missing = check_bot_permissions(member)
            invite_link = None
            if not missing:
                link_obj = await bot.create_chat_invite_link(chat_id, name="Boterator Managed Link")
                invite_link = link_obj.invite_link
            current = (await db.execute(select(ManagedChat).where(ManagedChat.chat_id == chat_id))).scalar_one_or_none()
            protect_content_enabled = bool(getattr(chat, "has_protected_content", False))
            if current:
                current.title = chat.title or f"Chat {chat_id}"
                current.is_active = True
                current.permissions_ok = len(missing) == 0
                current.missing_permissions = ", ".join(missing) if missing else None
                current.protect_content_enabled = protect_content_enabled
                if invite_link:
                    current.invite_link = invite_link
            else:
                db.add(
                    ManagedChat(
                        chat_id=chat_id,
                        title=chat.title or f"Chat {chat_id}",
                        invite_link=invite_link,
                        is_active=True,
                        permissions_ok=len(missing) == 0,
                        missing_permissions=", ".join(missing) if missing else None,
                        protect_content_enabled=protect_content_enabled,
                    )
                )
            await db.commit()
            return {"status": "ok", "message": f"Ресурс '{chat.title}' добавлен"}

        if action == "sync_resources":
            access_error = await ensure_staff(role, admin_only=True)
            if access_error:
                return access_error
            chats = (await db.execute(select(ManagedChat))).scalars().all()
            updated = 0
            for chat in chats:
                try:
                    tg_chat = await bot.get_chat(chat.chat_id)
                    member = await bot.get_chat_member(chat.chat_id, (await bot.get_me()).id)
                    missing = check_bot_permissions(member)
                    chat.permissions_ok = len(missing) == 0
                    chat.missing_permissions = ", ".join(missing) if missing else None
                    chat.is_active = True
                    chat.protect_content_enabled = bool(getattr(tg_chat, "has_protected_content", False))
                    if chat.permissions_ok and not chat.invite_link:
                        link_obj = await bot.create_chat_invite_link(chat.chat_id, name="Boterator Sync Link")
                        chat.invite_link = link_obj.invite_link
                    updated += 1
                except Exception as exc:
                    chat.is_active = False
                    logger.warning(f"Failed to sync chat {chat.chat_id}: {exc}")
            await db.commit()
            return {"status": "ok", "message": f"Обновлено ресурсов: {updated}"}

        if action == "issue_tariff":
            access_error = await ensure_staff(role)
            if access_error:
                return access_error
            target_id = int(payload.get("target_id") or 0)
            tariff_id = int(payload.get("tariff_id") or 0)
            if not target_id or not tariff_id:
                return bad_request_response("target_id и tariff_id обязательны")
            target_user = (await db.execute(select(User).where(User.telegram_id == target_id))).scalar_one_or_none()
            if not target_user:
                target_user = User(telegram_id=target_id)
                db.add(target_user)
                await db.flush()
            tariff = (await db.execute(select(Tariff).where(Tariff.id == tariff_id))).scalar_one_or_none()
            if not tariff:
                return bad_request_response("Тариф не найден")
            await activate_user_access(db, bot, user=target_user, tariff=tariff, paid=True)
            return {"status": "ok", "message": "Тариф выдан вручную"}

        if action == "revoke_tariff":
            access_error = await ensure_staff(role)
            if access_error:
                return access_error
            target_id = int(payload.get("target_id") or 0)
            tariff_id = int(payload.get("tariff_id") or 0)
            if not target_id or not tariff_id:
                return bad_request_response("target_id и tariff_id обязательны")
            await db.execute(
                update(Subscription)
                .where(Subscription.user_id == target_id, Subscription.tariff_id == tariff_id, Subscription.is_active.is_(True))
                .values(is_active=False, in_grace_period=False, grace_end_date=None)
            )
            await db.commit()
            await revoke_user_from_inaccessible_chats(bot, db, target_id)
            return {"status": "ok", "message": "Тариф снят"}

        if action == "promote_admin":
            access_error = await ensure_staff(role, super_only=True)
            if access_error:
                return access_error
            target_id = int(payload.get("target_id") or 0)
            await db.execute(update(User).where(User.telegram_id == target_id).values(is_admin=True, is_moderator=False))
            await db.commit()
            return {"status": "ok", "message": "Пользователь назначен администратором"}

        if action == "demote_admin":
            access_error = await ensure_staff(role, super_only=True)
            if access_error:
                return access_error
            target_id = int(payload.get("target_id") or 0)
            if target_id in settings.bot.admin_ids:
                return bad_request_response("Нельзя снять права super admin из конфига")
            await db.execute(update(User).where(User.telegram_id == target_id).values(is_admin=False))
            await db.commit()
            return {"status": "ok", "message": "Права администратора сняты"}

        if action == "set_moderator":
            access_error = await ensure_staff(role, admin_only=True)
            if access_error:
                return access_error
            target_id = int(payload.get("target_id") or 0)
            await db.execute(update(User).where(User.telegram_id == target_id).values(is_moderator=True, is_admin=False))
            await db.commit()
            return {"status": "ok", "message": "Пользователь назначен модератором"}

        if action == "unset_moderator":
            access_error = await ensure_staff(role, admin_only=True)
            if access_error:
                return access_error
            target_id = int(payload.get("target_id") or 0)
            await db.execute(update(User).where(User.telegram_id == target_id).values(is_moderator=False))
            await db.commit()
            return {"status": "ok", "message": "Права модератора сняты"}

        if action == "kick_user":
            access_error = await ensure_staff(role)
            if access_error:
                return access_error
            target_id = int(payload.get("target_id") or 0)
            reason = (payload.get("reason") or "Нарушение правил").strip()
            await db.execute(
                update(Subscription)
                .where(Subscription.user_id == target_id, Subscription.is_active.is_(True))
                .values(is_active=False, in_grace_period=False, grace_end_date=None)
            )
            await db.commit()
            await revoke_user_from_inaccessible_chats(bot, db, target_id)
            try:
                await bot.send_message(target_id, f"<b>Доступ отозван</b>\nПричина: {reason}")
            except Exception as exc:
                logger.warning(f"Failed to notify kicked user {target_id}: {exc}")
            return {"status": "ok", "message": "Пользователь исключен"}

        if action == "export_csv":
            access_error = await ensure_staff(role)
            if access_error:
                return access_error
            await bot.send_document(user.telegram_id, await build_users_csv(db), caption="Экспорт пользователей")
            return {"status": "ok", "message": "CSV отправлен в личные сообщения"}

        if action == "broadcast":
            access_error = await ensure_staff(role)
            if access_error:
                return access_error
            text = (payload.get("text") or "").strip()
            target = payload.get("target", "all")
            if not text:
                return bad_request_response("Текст рассылки пуст")
            if target == "all":
                stmt = select(User.telegram_id)
            elif target == "tariff" and payload.get("tariff_id"):
                stmt = select(Subscription.user_id).where(
                    Subscription.is_active.is_(True),
                    Subscription.tariff_id == int(payload["tariff_id"]),
                ).distinct()
            elif target == "expiring":
                border = utcnow() + timedelta(days=3)
                stmt = select(Subscription.user_id).where(
                    Subscription.is_active.is_(True),
                    Subscription.end_date.is_not(None),
                    Subscription.end_date <= border,
                ).distinct()
            else:
                stmt = select(Subscription.user_id).where(Subscription.is_active.is_(True)).distinct()
            user_ids = [int(uid) for uid in (await db.execute(stmt)).scalars().all()]
            background_tasks.add_task(run_broadcast_task, bot, user_ids, text)
            return {"status": "ok", "message": f"Рассылка запущена на {len(user_ids)} пользователей"}

        if action == "confirm_manual_payment":
            access_error = await ensure_staff(role)
            if access_error:
                return access_error
            payment_id = int(payload.get("payment_id") or 0)
            payment = (await db.execute(select(Payment).where(Payment.id == payment_id))).scalar_one_or_none()
            if not payment:
                return bad_request_response("Платеж не найден")
            if payment.status == "success":
                return {"status": "ok", "message": "Платеж уже подтвержден"}
            return await finalize_successful_payment(db, request, payment)

        return bad_request_response("Неизвестное действие")
    except Exception as exc:
        logger.error(f"Action processing error: {exc}")
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)



