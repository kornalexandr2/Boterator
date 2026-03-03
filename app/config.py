import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from loguru import logger

class BotSettings(BaseSettings):
    token: str = ""
    admin_ids: list[int] = []
    grace_period_days: int = 3

class DatabaseSettings(BaseSettings):
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    db_name: str = "boterator"

    @property
    def url(self) -> str:
        if not self.password or not self.db_name:
            return ""
        return f"mysql+aiomysql://{self.user}:{self.password}@{self.host}:{self.port}/{self.db_name}"

class PaymentSettings(BaseSettings):
    mock_mode: bool = True
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    sberbank_username: str = ""
    sberbank_password: str = ""
    yoomoney_receiver: str = ""

class AppSettings(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 8000
    base_url: str = "http://127.0.0.1:8000"
    secret_key: str = "replace-me-in-production"

class Config(BaseSettings):
    bot: BotSettings = BotSettings()
    db: DatabaseSettings = DatabaseSettings()
    payments: PaymentSettings = PaymentSettings()
    app: AppSettings = AppSettings()

    model_config = SettingsConfigDict(
        env_file=os.path.join("DEVELOPE", ".env"), 
        env_nested_delimiter="__", 
        extra="ignore"
    )

def load_config() -> Config:
    config = Config()
    
    # Graceful degradation checks
    if not config.bot.token:
        logger.warning("BOT_TOKEN is missing! The bot will not start or will operate with severely limited functionality.")
    
    if not config.db.url:
         logger.warning("Database configuration is missing! Data persistence will fail.")
         
    if config.payments.mock_mode:
        logger.info("Payments are running in MOCK mode.")
    elif not any([config.payments.yookassa_shop_id, config.payments.sberbank_username, config.payments.yoomoney_receiver]):
        logger.warning("Production payment mode enabled, but no payment credentials found! Payments will fail.")

    return config

settings = load_config()
