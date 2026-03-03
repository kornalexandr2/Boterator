from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
import sys

from app.config import settings
from app.database.session import init_models
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.middlewares.db import DbSessionMiddleware
from app.bot.handlers import commands, join_requests
from app.api import routes as api_routes

# Configure Loguru
logger.remove()
logger.add(sys.stdout, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")
logger.add("logs/boterator.log", rotation="10 MB", retention="10 days", level="INFO")

# Initialize Bot (Graceful degradation if token is missing)
bot = None
dp = None

if settings.bot.token:
    try:
        bot = Bot(token=settings.bot.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dp = Dispatcher()
        
        # Register middlewares
        dp.update.middleware(DbSessionMiddleware())
        
        # Register routers
        dp.include_router(commands.router)
        dp.include_router(join_requests.router)
        
        logger.info("Bot instance initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize bot: {e}")
else:
    logger.warning("Bot token not found. Bot functionality will be disabled.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Boterator application...")
    await init_models()
    
    if bot and dp:
         webhook_url = f"{settings.app.base_url}/webhook"
         try:
             await bot.set_webhook(webhook_url)
             logger.info(f"Webhook set to {webhook_url}")
         except Exception as e:
             logger.error(f"Failed to set webhook: {e}")
    else:
         logger.warning("Skipping webhook setup as Bot is not initialized.")
         
    yield
    # Shutdown
    logger.info("Shutting down Boterator application...")
    if bot:
         try:
             await bot.delete_webhook()
             await bot.session.close()
             logger.info("Webhook deleted and bot session closed.")
         except Exception as e:
             logger.error(f"Error during bot shutdown: {e}")

app = FastAPI(title="Boterator API", lifespan=lifespan)
app.include_router(api_routes.router)

@app.get("/", response_class=HTMLResponse)
async def root():
    return "<h1>Boterator is running</h1><p>Check logs for warnings if configuration is missing.</p>"

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Handles incoming updates from Telegram."""
    if not bot or not dp:
        logger.error("Webhook received but bot is not initialized.")
        return {"status": "error", "message": "Bot not configured"}
    
    update_data = await request.json()
    from aiogram.types import Update
    try:
        update = Update(**update_data)
        await dp.feed_update(bot=bot, update=update)
    except Exception as e:
         logger.error(f"Failed to process update: {e}")
         return {"status": "error", "message": str(e)}
         
    return {"status": "ok"}
