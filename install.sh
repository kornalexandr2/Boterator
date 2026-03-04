#!/bin/bash
set -e

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}Начинаем автоматическую установку Boterator...${NC}"

# Проверка на root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Пожалуйста, запустите скрипт через sudo.${NC}"
  exit 1
fi

PROJECT_DIR="/opt/boterator"

# Клонирование или обновление репозитория
if [ ! -d "$PROJECT_DIR/.git" ]; then
    echo "Клонирование репозитория..."
    apt-get update && apt-get install -y git
    if [ -d "$PROJECT_DIR" ]; then rm -rf "$PROJECT_DIR"; fi
    git clone https://github.com/kornalexandr2/Boterator.git "$PROJECT_DIR"
fi

cd "$PROJECT_DIR"
git fetch origin master && git reset --hard origin/master

DEVELOPE_DIR="$PROJECT_DIR/DEVELOPE"
mkdir -p "$DEVELOPE_DIR"
ENV_FILE="$DEVELOPE_DIR/.env"
CRED_FILE="$DEVELOPE_DIR/mysql_credentials.txt"

# Запрос данных у пользователя
echo -e "\n${YELLOW}=== Настройка Boterator ===${NC}"
echo -n "Введите Telegram Bot Token: "
read BOT_TOKEN < /dev/tty
echo -n "Введите ID Администратора (число): "
read ADMIN_ID < /dev/tty

# Настройка MySQL
echo "Настройка MySQL сервера..."
apt-get update && apt-get install -y mysql-server

# ПОПЫТКА СБРОСА ПАРОЛЯ (чтобы скрипт мог работать дальше)
echo "Обеспечение доступа к MySQL..."
# Пытаемся войти всеми способами и установить auth_socket для root
sudo mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH auth_socket; FLUSH PRIVILEGES;" || \
mysql -u root -p1 -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH auth_socket; FLUSH PRIVILEGES;" || \
echo "Предупреждение: Не удалось сбросить пароль root, попробуем продолжить..."

# Генерируем данные для новой базы
MYSQL_USER="boterator_user"
MYSQL_PASS=$(openssl rand -hex 12)
MYSQL_DB="boterator"

echo "Создание пользователя и базы данных..."
# Используем sudo для доступа через сокет
sudo mysql <<EOF
DROP DATABASE IF EXISTS $MYSQL_DB;
CREATE DATABASE $MYSQL_DB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
DROP USER IF EXISTS '$MYSQL_USER'@'localhost';
CREATE USER '$MYSQL_USER'@'localhost' IDENTIFIED BY '$MYSQL_PASS';
GRANT ALL PRIVILEGES ON $MYSQL_DB.* TO '$MYSQL_USER'@'localhost';
FLUSH PRIVILEGES;
EOF

# Сохранение учетных данных
cat <<EOF > "$CRED_FILE"
=== MySQL Credentials ===
Database: $MYSQL_DB
User:     $MYSQL_USER
Password: $MYSQL_PASS
Host:     localhost
Port:     3306
EOF

# Генерация .env
cat <<EOF > "$ENV_FILE"
BOT__TOKEN=$BOT_TOKEN
BOT__ADMIN_IDS=[$ADMIN_ID]
DB__USER=$MYSQL_USER
DB__PASSWORD=$MYSQL_PASS
DB__DB_NAME=$MYSQL_DB
DB__HOST=localhost
DB__PORT=3306
APP__SECRET_KEY=$(openssl rand -hex 32)
APP__BASE_URL=http://$(hostname -I | awk '{print $1}'):8000
PAYMENTS__MOCK_MODE=True
EOF

# Настройка Python окружения
echo "Установка зависимостей Python..."
apt-get install -y python3-venv python3-pip
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Создание службы
SERVICE_FILE="/etc/systemd/system/boterator.service"
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
systemctl enable boterator
systemctl restart boterator
ufw allow 8000 || true

echo -e "
${GREEN}=== УСТАНОВКА ЗАВЕРШЕНА! ===${NC}
${YELLOW}Сайт доступен по адресу:${NC} http://$(hostname -I | awk '{print $1}'):8000

${YELLOW}Данные для доступа к базе данных:${NC}
Адрес:   localhost:3306
База:    $MYSQL_DB
Логин:   $MYSQL_USER
Пароль:  $MYSQL_PASS

${YELLOW}Конфигурация сохранена в:${NC} $ENV_FILE
${YELLOW}Учетки БД сохранены в:${NC} $CRED_FILE

Проверить статус: systemctl status boterator
"
