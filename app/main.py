import sys
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from app.api import routes as api_routes
from app.bot.handlers import admin_events, commands, join_requests
from app.bot.middlewares.db import DbSessionMiddleware
from app.bot.tasks import start_background_tasks
from app.config import settings
from app.database.session import init_models

logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
)
logger.add("logs/boterator.log", rotation="10 MB", retention="10 days", level="INFO")

bot = None
dp = None
if settings.bot.token:
    try:
        bot = Bot(token=settings.bot.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dp = Dispatcher()
        dp.update.middleware(DbSessionMiddleware())
        dp.include_router(commands.router)
        dp.include_router(join_requests.router)
        dp.include_router(admin_events.router)
        logger.info("Bot instance initialized.")
    except Exception as exc:
        logger.error(f"Failed to initialize bot: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Boterator application...")
    await init_models()
    if bot and dp:
        webhook_url = f"{settings.app.base_url.rstrip('/')}/webhook"
        try:
            await bot.set_webhook(webhook_url)
            logger.info(f"Webhook set to {webhook_url}")
            start_background_tasks(bot)
        except Exception as exc:
            logger.error(f"Failed to set webhook: {exc}")
    yield
    if bot:
        await bot.delete_webhook()
        await bot.session.close()


app = FastAPI(title="Boterator API", lifespan=lifespan)
app.state.bot = bot
app.include_router(api_routes.router)


@app.get("/", response_class=HTMLResponse)
async def root():
    return "<h1>Boterator is running</h1><p>/twa/store - storefront, /twa/admin - CRM</p>"


@app.post("/webhook")
async def telegram_webhook(request: Request):
    if not bot or not dp:
        return {"status": "error", "message": "Bot is unavailable"}
    payload = await request.json()
    from aiogram.types import Update

    try:
        update = Update(**payload)
        await dp.feed_update(bot=bot, update=update)
    except Exception as exc:
        logger.error(f"Update error: {exc}")
    return {"status": "ok"}
