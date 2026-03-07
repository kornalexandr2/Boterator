import asyncio

from loguru import logger

from app.database.session import init_models


async def fix_database():
    logger.info("Запуск проверки и обновления структуры БД...")
    await init_models()
    logger.info("Проверка структуры БД завершена.")


if __name__ == "__main__":
    asyncio.run(fix_database())
