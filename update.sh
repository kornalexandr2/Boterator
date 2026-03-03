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

# Git pull
echo "Загрузка обновлений из репозитория..."
git pull origin main

# Update dependencies
echo "Обновление зависимостей..."
source venv/bin/activate
pip install -r requirements.txt

# Restart service
echo "Перезапуск службы systemd..."
systemctl restart boterator.service

echo -e "${GREEN}Обновление успешно завершено!${NC}"
