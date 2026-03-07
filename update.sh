#!/bin/bash
set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Пожалуйста, запустите скрипт от имени root (sudo).${NC}"
  exit 1
fi

echo "Начинаем обновление Boterator..."

PROJECT_DIR="/opt/boterator"
cd "$PROJECT_DIR"

# Git update
# Обновляем ветку main без принудительного удаления локальных файлов.
echo "Загрузка обновлений из репозитория..."
git pull origin main

# Update dependencies
echo "Обновление зависимостей..."
source venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

# Перезапуск службы с сохранением текущих настроек в DEVELOPE/
echo "Перезапуск службы..."
systemctl restart boterator.service

echo -e "${GREEN}Обновление успешно завершено!${NC}"
