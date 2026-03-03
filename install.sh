#!/bin/bash
set -e

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}Начинаем установку Boterator...${NC}"

# Проверка на root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Пожалуйста, запустите скрипт от имени root (sudo).${NC}"
  exit 1
fi

PROJECT_DIR="/opt/boterator"

if [ ! -d "$PROJECT_DIR" ]; then
    echo "Клонирование репозитория в $PROJECT_DIR..."
    apt-get update && apt-get install -y git
    git clone https://github.com/kornalexandr2/Boterator.git "$PROJECT_DIR"
fi

cd "$PROJECT_DIR"

DEVELOPE_DIR="$PROJECT_DIR/DEVELOPE"
ENV_FILE="$DEVELOPE_DIR/.env"

# Создание папки DEVELOPE
mkdir -p "$DEVELOPE_DIR"

# Проверка наличия программы
if [ -f "$ENV_FILE" ]; then
    echo -e "${YELLOW}Файл конфигурации уже существует. Программа уже установлена?${NC}"
    read -p "Продолжить и перезаписать конфиг? (y/N): " CONTINUE
    if [[ "$CONTINUE" != "y" && "$CONTINUE" != "Y" ]]; then
        echo "Установка прервана."
        exit 0
    fi
fi

# Запрос данных у пользователя
echo -e "
${YELLOW}=== Настройка Boterator ===${NC}"
echo "Оставьте поле пустым и нажмите Enter, чтобы пропустить (будут использованы дефолтные значения/пустота)."

read -p "Введите Telegram Bot Token: " BOT_TOKEN
read -p "Введите ID Администратора (число): " ADMIN_ID

# Настройка MySQL
read -p "Установить и настроить MySQL локально автоматически? (Y/n): " AUTO_MYSQL
if [[ "$AUTO_MYSQL" != "n" && "$AUTO_MYSQL" != "N" ]]; then
    MYSQL_USER="boterator_user"
    MYSQL_PASS=$(openssl rand -hex 12)
    MYSQL_DB="boterator_db"
    
    echo "Установка MySQL сервера (если не установлен)..."
    apt-get update && apt-get install -y mysql-server
    
    echo "Настройка базы данных и пользователя..."
    mysql -e "CREATE DATABASE IF NOT EXISTS $MYSQL_DB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
    mysql -e "CREATE USER IF NOT EXISTS '$MYSQL_USER'@'localhost' IDENTIFIED BY '$MYSQL_PASS';"
    mysql -e "GRANT ALL PRIVILEGES ON $MYSQL_DB.* TO '$MYSQL_USER'@'localhost';"
    mysql -e "FLUSH PRIVILEGES;"
    
    echo -e "${GREEN}MySQL настроен автоматически.${NC}"
    echo -e "Пользователь: $MYSQL_USER
Пароль: $MYSQL_PASS
База: $MYSQL_DB" > "$DEVELOPE_DIR/mysql_credentials.txt"
    echo "Реквизиты сохранены в $DEVELOPE_DIR/mysql_credentials.txt"
else
    read -p "Введите MySQL User: " MYSQL_USER
    read -p "Введите MySQL Password: " MYSQL_PASS
    read -p "Введите MySQL Database Name: " MYSQL_DB
fi

# Генерация .env файла
echo "Генерация файла конфигурации..."
cat <<EOF > "$ENV_FILE"
BOT__TOKEN=$BOT_TOKEN
BOT__ADMIN_IDS=[$ADMIN_ID]

DB__USER=$MYSQL_USER
DB__PASSWORD=$MYSQL_PASS
DB__DB_NAME=$MYSQL_DB
DB__HOST=localhost
DB__PORT=3306

APP__SECRET_KEY=$(openssl rand -hex 32)
PAYMENTS__MOCK_MODE=True
EOF

echo -e "${GREEN}Конфигурация сохранена в $ENV_FILE${NC}"

# Настройка Python окружения
echo "Установка зависимостей Python..."
apt-get install -y python3-venv python3-pip
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Создание службы systemd
SERVICE_FILE="/etc/systemd/system/boterator.service"
echo "Создание systemd службы..."

cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Boterator Daemon
After=network.target mysql.service

[Service]
User=root
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$PROJECT_DIR/venv/bin"
ExecStart=$PROJECT_DIR/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable boterator.service
systemctl restart boterator.service

echo -e "
${GREEN}=== Установка завершена! ===${NC}"
echo "Boterator запущен как служба 'boterator.service'."
echo "Проверить статус: systemctl status boterator"
echo "Логи: journalctl -u boterator -f"
