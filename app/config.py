import os
import yaml
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
        extra="ignore",
    )


def load_config() -> Config:
    config = Config()

    yaml_path = os.path.join("DEVELOPE", "config.yaml")
    if os.path.exists(yaml_path):
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f)
                if yaml_data:
                    if "bot" in yaml_data:
                        config.bot = BotSettings(**yaml_data["bot"])
                    if "database" in yaml_data:
                        config.db = DatabaseSettings(**yaml_data["database"])
                    if "payments" in yaml_data:
                        pay_data = yaml_data["payments"] or {}
                        config.payments.mock_mode = pay_data.get("mock_mode", config.payments.mock_mode)
                        yk_data = pay_data.get("yookassa", {}) or {}
                        sb_data = pay_data.get("sberbank", {}) or {}
                        ym_data = pay_data.get("yoomoney", {}) or {}

                        config.payments.yookassa_shop_id = yk_data.get("shop_id", config.payments.yookassa_shop_id)
                        config.payments.yookassa_secret_key = yk_data.get("secret_key", config.payments.yookassa_secret_key)
                        config.payments.sberbank_username = sb_data.get("username", config.payments.sberbank_username)
                        config.payments.sberbank_password = sb_data.get("password", config.payments.sberbank_password)
                        config.payments.yoomoney_receiver = ym_data.get(
                            "receiver_wallet",
                            ym_data.get("receiver", config.payments.yoomoney_receiver),
                        )
                    if "app" in yaml_data:
                        config.app = AppSettings(**yaml_data["app"])
                    logger.info(f"Configuration loaded from {yaml_path}")
        except Exception as e:
            logger.error(f"Failed to load configuration from {yaml_path}: {e}")

    if not config.bot.token or "YOUR_BOT_TOKEN_HERE" in config.bot.token:
        logger.warning("BOT_TOKEN is missing or default! The bot will not start or will operate with severely limited functionality.")

    if not config.db.url:
        logger.warning("Database configuration is missing! Data persistence will fail.")

    if config.payments.mock_mode:
        logger.info("Payments are running in MOCK mode.")
    elif not any([
        config.payments.yookassa_shop_id,
        config.payments.sberbank_username,
        config.payments.yoomoney_receiver,
    ]):
        logger.warning("Production payment mode enabled, but no payment credentials found! Payments will fail.")

    return config


settings = load_config()
