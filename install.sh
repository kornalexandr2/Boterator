#!/bin/bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

PROJECT_DIR="/opt/boterator"
REPO_URL="https://github.com/kornalexandr2/Boterator.git"
SERVICE_USER="boterator"
DEVELOPE_DIR="$PROJECT_DIR/DEVELOPE"
CONFIG_FILE="$DEVELOPE_DIR/config.yaml"
CRED_FILE="$DEVELOPE_DIR/mysql_credentials.txt"
SERVICE_FILE="/etc/systemd/system/boterator.service"

resolve_branch() {
    if git ls-remote --exit-code --heads "$REPO_URL" main >/dev/null 2>&1; then
        echo main
        return
    fi
    if git ls-remote --exit-code --heads "$REPO_URL" master >/dev/null 2>&1; then
        echo master
        return
    fi
    echo main
}

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Запустите install.sh через sudo.${NC}"
    exit 1
fi

BRANCH="$(resolve_branch)"

echo -e "${GREEN}Установка Boterator...${NC}"
apt-get update
apt-get install -y git python3 python3-venv python3-pip curl

if id -u "$SERVICE_USER" >/dev/null 2>&1; then
    echo "Системный пользователь $SERVICE_USER уже существует."
else
    useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

if [ -d "$PROJECT_DIR/.git" ]; then
    echo "Проект уже установлен. Обновляем код."
    git -C "$PROJECT_DIR" fetch origin "$BRANCH"
    git -C "$PROJECT_DIR" checkout "$BRANCH"
    git -C "$PROJECT_DIR" pull --ff-only origin "$BRANCH"
elif [ -d "$PROJECT_DIR" ] && [ -n "$(ls -A "$PROJECT_DIR" 2>/dev/null || true)" ]; then
    echo -e "${RED}Каталог $PROJECT_DIR уже существует и не является git-репозиторием. Очистите его вручную или перенесите данные.${NC}"
    exit 1
else
    rm -rf "$PROJECT_DIR"
    git clone --branch "$BRANCH" "$REPO_URL" "$PROJECT_DIR"
fi

mkdir -p "$DEVELOPE_DIR"

echo
read -r -p "Telegram Bot Token (Enter для пропуска): " BOT_TOKEN
read -r -p "Telegram Admin ID (Enter для пропуска): " ADMIN_ID
read -r -p "Base URL приложения, например https://example.com (Enter для авто): " BASE_URL
read -r -p "MySQL host (Enter для локальной установки и автонастройки): " DB_HOST

DB_PORT="3306"
DB_USER=""
DB_PASSWORD=""
DB_NAME="boterator"

if [ -z "$DB_HOST" ]; then
    echo "Настраиваем локальный MySQL..."
    apt-get install -y mysql-server
    systemctl enable mysql
    systemctl restart mysql

    DB_HOST="localhost"
    DB_USER="boterator_user"
    DB_PASSWORD="$(openssl rand -hex 18)"
    DB_NAME="boterator"

    mysql <<SQL
CREATE DATABASE IF NOT EXISTS $DB_NAME CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASSWORD';
ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASSWORD';
GRANT ALL PRIVILEGES ON $DB_NAME.* TO '$DB_USER'@'localhost';
FLUSH PRIVILEGES;
SQL

    cat > "$CRED_FILE" <<EOF
MySQL host: $DB_HOST
MySQL port: $DB_PORT
MySQL database: $DB_NAME
MySQL user: $DB_USER
MySQL password: $DB_PASSWORD
EOF
else
    read -r -p "MySQL port (по умолчанию 3306): " DB_PORT_INPUT
    read -r -p "MySQL database (по умолчанию boterator): " DB_NAME_INPUT
    read -r -p "MySQL user: " DB_USER
    read -r -p "MySQL password: " DB_PASSWORD
    DB_PORT="${DB_PORT_INPUT:-3306}"
    DB_NAME="${DB_NAME_INPUT:-boterator}"
fi

if [ -z "$BASE_URL" ]; then
    BASE_URL="http://$(hostname -I | awk '{print $1}'):8000"
fi

ADMIN_IDS="[]"
if [ -n "$ADMIN_ID" ]; then
    ADMIN_IDS="[$ADMIN_ID]"
fi

cat > "$CONFIG_FILE" <<EOF
bot:
  token: "$BOT_TOKEN"
  admin_ids: $ADMIN_IDS
  grace_period_days: 3

database:
  host: "$DB_HOST"
  port: $DB_PORT
  user: "$DB_USER"
  password: "$DB_PASSWORD"
  db_name: "$DB_NAME"

payments:
  mock_mode: true
  yookassa:
    shop_id: ""
    secret_key: ""
  sberbank:
    username: ""
    password: ""
  yoomoney:
    receiver_wallet: ""

app:
  host: "0.0.0.0"
  port: 8000
  base_url: "$BASE_URL"
  secret_key: "$(openssl rand -hex 32)"
EOF

python3 -m venv "$PROJECT_DIR/venv"
source "$PROJECT_DIR/venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$PROJECT_DIR/requirements.txt"

chown -R "$SERVICE_USER":"$SERVICE_USER" "$PROJECT_DIR"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Boterator Daemon
After=network.target mysql.service

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$PROJECT_DIR/venv/bin"
ExecStart=$PROJECT_DIR/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable boterator.service
systemctl restart boterator.service

echo -e "${GREEN}Установка завершена.${NC}"
echo -e "${YELLOW}Конфиг: ${CONFIG_FILE}${NC}"
if [ -f "$CRED_FILE" ]; then
    echo -e "${YELLOW}Сгенерированные MySQL доступы: ${CRED_FILE}${NC}"
fi
echo -e "${YELLOW}Проверьте сервис: systemctl status boterator.service${NC}"
echo -e "${YELLOW}Текущий base_url: ${BASE_URL}${NC}"
