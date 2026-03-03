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

# Если папка не является git-репозиторием или пуста, клонируем
if [ ! -d "$PROJECT_DIR/.git" ]; then
    echo "Клонирование репозитория в $PROJECT_DIR..."
    apt-get update && apt-get install -y git
    
    if [ -d "$PROJECT_DIR" ]; then
        # Если папка существует, но не репозиторий, клонируем во временную и переносим
        TEMP_DIR=$(mktemp -d)
        git clone https://github.com/kornalexandr2/Boterator.git "$TEMP_DIR"
        cp -rn "$TEMP_DIR/." "$PROJECT_DIR/"
        rm -rf "$TEMP_DIR"
    else
        git clone https://github.com/kornalexandr2/Boterator.git "$PROJECT_DIR"
    fi
fi

cd "$PROJECT_DIR"

# Гарантируем наличие актуальных файлов скриптов
git pull origin master || echo "Не удалось обновить через git pull, продолжаем с текущими файлами."

DEVELOPE_DIR="$PROJECT_DIR/DEVELOPE"
ENV_FILE="$DEVELOPE_DIR/.env"

# Создание папки DEVELOPE
mkdir -p "$DEVELOPE_DIR"

# Проверка наличия конфигурации
if [ -f "$ENV_FILE" ]; then
    echo -e "${YELLOW}Файл конфигурации уже существует.${NC}"
    echo -n "Продолжить и перезаписать конфиг? (y/N): "
    read CONTINUE < /dev/tty
    if [[ "$CONTINUE" != "y" && "$CONTINUE" != "Y" ]]; then
        echo "Использование существующей конфигурации."
    else
        # Запрос данных у пользователя (только если решили перезаписать)
        echo -e "\n${YELLOW}=== Настройка Boterator ===${NC}"
        echo "Оставьте поле пустым, чтобы не менять значение."

        echo -n "Введите Telegram Bot Token: "
        read BOT_TOKEN < /dev/tty
        echo -n "Введите ID Администратора (число): "
        read ADMIN_ID < /dev/tty

        # Настройка MySQL
        echo -n "Установить и настроить MySQL локально автоматически? (Y/n): "
        read AUTO_MYSQL < /dev/tty

        if [[ "$AUTO_MYSQL" != "n" && "$AUTO_MYSQL" != "N" ]]; then
            MYSQL_USER="boterator_user"
            MYSQL_PASS=$(openssl rand -hex 12)
            MYSQL_DB="boterator_db"
            
            echo "Установка MySQL сервера..."
            apt-get update && apt-get install -y mysql-server
            
            echo "Настройка базы данных и пользователя..."
            mysql -e "CREATE DATABASE IF NOT EXISTS $MYSQL_DB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
            mysql -e "CREATE USER IF NOT EXISTS '$MYSQL_USER'@'localhost' IDENTIFIED BY '$MYSQL_PASS';"
            mysql -e "GRANT ALL PRIVILEGES ON $MYSQL_DB.* TO '$MYSQL_USER'@'localhost';"
            mysql -e "FLUSH PRIVILEGES;"
            
            echo -e "Пользователь: $MYSQL_USER\nПароль: $MYSQL_PASS\nБаза: $MYSQL_DB" > "$DEVELOPE_DIR/mysql_credentials.txt"
        else
            echo -n "Введите MySQL User: "
            read MYSQL_USER < /dev/tty
            echo -n "Введите MySQL Password: "
            read MYSQL_PASS < /dev/tty
            echo -n "Введите MySQL Database Name: "
            read MYSQL_DB < /dev/tty
        fi

        # Генерация .env файла
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
    fi
else
    # Первая установка - обязательный запрос данных
    echo -e "\n${YELLOW}=== Настройка Boterator ===${NC}"
    echo -n "Введите Telegram Bot Token: "
    read BOT_TOKEN < /dev/tty
    echo -n "Введите ID Администратора (число): "
    read ADMIN_ID < /dev/tty

    MYSQL_USER="boterator_user"
    MYSQL_PASS=$(openssl rand -hex 12)
    MYSQL_DB="boterator_db"
    
    echo "Автоматическая настройка MySQL..."
    apt-get update && apt-get install -y mysql-server
    mysql -e "CREATE DATABASE IF NOT EXISTS $MYSQL_DB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
    mysql -e "CREATE USER IF NOT EXISTS '$MYSQL_USER'@'localhost' IDENTIFIED BY '$MYSQL_PASS';"
    mysql -e "GRANT ALL PRIVILEGES ON $MYSQL_DB.* TO '$MYSQL_USER'@'localhost';"
    mysql -e "FLUSH PRIVILEGES;"
    
    echo -e "Пользователь: $MYSQL_USER\nПароль: $MYSQL_PASS\nБаза: $MYSQL_DB" > "$DEVELOPE_DIR/mysql_credentials.txt"

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
fi

# Настройка Python окружения
echo "Установка зависимостей Python..."
apt-get install -y python3-venv python3-pip
python3 -m venv venv
source venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

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
echo "Проверить статус: systemctl status boterator"
