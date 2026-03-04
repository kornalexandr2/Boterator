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
echo "Загрузка обновлений из репозитория..."
# Принудительно обновляем код, игнорируя локальные изменения на сервере
git fetch origin master
git reset --hard origin/master

# Update dependencies
echo "Обновление зависимостей..."
source venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

# Обновление службы systemd (на случай изменения параметров запуска)
SERVICE_FILE="/etc/systemd/system/boterator.service"
echo "Обновление конфигурации systemd службы..."

cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Boterator Daemon
After=network.target mysql.service

[Service]
User=root
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$PROJECT_DIR/venv/bin"
ExecStart=$PROJECT_DIR/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
echo "Перезапуск службы..."
systemctl restart boterator.service

echo -e "${GREEN}Обновление успешно завершено!${NC}"
