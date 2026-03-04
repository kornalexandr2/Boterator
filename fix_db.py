import asyncio
from sqlalchemy import text
from app.database.session import engine
from loguru import logger

async def fix_database():
    logger.info("Начинаем обновление структуры базы данных...")
    
    async with engine.begin() as conn:
        # Список команд для обновления таблицы managed_chats
        commands = [
            "ALTER TABLE managed_chats ADD COLUMN permissions_ok BOOLEAN DEFAULT 1",
            "ALTER TABLE managed_chats ADD COLUMN missing_permissions TEXT"
        ]
        
        for cmd in commands:
            try:
                await conn.execute(text(cmd))
                logger.info(f"Выполнено: {cmd}")
            except Exception as e:
                if "Duplicate column name" in str(e):
                    logger.info(f"Колонка уже существует, пропускаем.")
                else:
                    logger.error(f"Ошибка при выполнении '{cmd}': {e}")

    logger.info("Обновление завершено успешно.")

if __name__ == "__main__":
    asyncio.run(fix_database())
