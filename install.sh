#!/bin/bash
set -e

# Р¦РІРµС‚Р° РґР»СЏ РІС‹РІРѕРґР°
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}РќР°С‡РёРЅР°РµРј Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєСѓСЋ СѓСЃС‚Р°РЅРѕРІРєСѓ Boterator...${NC}"

# РџСЂРѕРІРµСЂРєР° РЅР° root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}РџРѕР¶Р°Р»СѓР№СЃС‚Р°, Р·Р°РїСѓСЃС‚РёС‚Рµ СЃРєСЂРёРїС‚ С‡РµСЂРµР· sudo.${NC}"
  exit 1
fi

PROJECT_DIR="/opt/boterator"

# РљР»РѕРЅРёСЂРѕРІР°РЅРёРµ РёР»Рё РѕР±РЅРѕРІР»РµРЅРёРµ СЂРµРїРѕР·РёС‚РѕСЂРёСЏ
if [ ! -d "$PROJECT_DIR/.git" ]; then
    echo "РљР»РѕРЅРёСЂРѕРІР°РЅРёРµ СЂРµРїРѕР·РёС‚РѕСЂРёСЏ..."
    apt-get update && apt-get install -y git
    if [ -d "$PROJECT_DIR" ]; then rm -rf "$PROJECT_DIR"; fi
    git clone https://github.com/kornalexandr2/Boterator.git "$PROJECT_DIR"
fi

cd "$PROJECT_DIR"
git pull origin main

DEVELOPE_DIR="$PROJECT_DIR/DEVELOPE"
mkdir -p "$DEVELOPE_DIR"
ENV_FILE="$DEVELOPE_DIR/.env"
CRED_FILE="$DEVELOPE_DIR/mysql_credentials.txt"

# Р—Р°РїСЂРѕСЃ РґР°РЅРЅС‹С… Сѓ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ
echo -e "\n${YELLOW}=== РќР°СЃС‚СЂРѕР№РєР° Boterator ===${NC}"
echo -n "Р’РІРµРґРёС‚Рµ Telegram Bot Token: "
read BOT_TOKEN < /dev/tty
echo -n "Р’РІРµРґРёС‚Рµ ID РђРґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР° (С‡РёСЃР»Рѕ): "
read ADMIN_ID < /dev/tty

# РќР°СЃС‚СЂРѕР№РєР° MySQL
echo "РќР°СЃС‚СЂРѕР№РєР° MySQL СЃРµСЂРІРµСЂР°..."
apt-get update && apt-get install -y mysql-server

# РџРћРџР«РўРљРђ РЎР‘Р РћРЎРђ РџРђР РћР›РЇ (С‡С‚РѕР±С‹ СЃРєСЂРёРїС‚ РјРѕРі СЂР°Р±РѕС‚Р°С‚СЊ РґР°Р»СЊС€Рµ)
echo "РћР±РµСЃРїРµС‡РµРЅРёРµ РґРѕСЃС‚СѓРїР° Рє MySQL..."
sudo mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH auth_socket; FLUSH PRIVILEGES;" 2>/dev/null || true

# Р“РµРЅРµСЂРёСЂСѓРµРј РґР°РЅРЅС‹Рµ РґР»СЏ РЅРѕРІРѕР№ Р±Р°Р·С‹
MYSQL_USER="boterator_user"
MYSQL_PASS=$(openssl rand -hex 12)
MYSQL_DB="boterator"

echo "РЎРѕР·РґР°РЅРёРµ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ Рё Р±Р°Р·С‹ РґР°РЅРЅС‹С…..."
# РСЃРїРѕР»СЊР·СѓРµРј sudo РґР»СЏ РґРѕСЃС‚СѓРїР° С‡РµСЂРµР· СЃРѕРєРµС‚
sudo mysql <<EOF
CREATE DATABASE IF NOT EXISTS $MYSQL_DB CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
DROP USER IF EXISTS '$MYSQL_USER'@'localhost';
CREATE USER '$MYSQL_USER'@'localhost' IDENTIFIED BY '$MYSQL_PASS';
GRANT ALL PRIVILEGES ON $MYSQL_DB.* TO '$MYSQL_USER'@'localhost';
FLUSH PRIVILEGES;
EOF

# РЎРѕС…СЂР°РЅРµРЅРёРµ СѓС‡РµС‚РЅС‹С… РґР°РЅРЅС‹С…
cat <<EOF > "$CRED_FILE"
=== MySQL Credentials ===
Database: $MYSQL_DB
User:     $MYSQL_USER
Password: $MYSQL_PASS
Host:     localhost
Port:     3306
EOF

# Р“РµРЅРµСЂР°С†РёСЏ .env
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

# РќР°СЃС‚СЂРѕР№РєР° Python РѕРєСЂСѓР¶РµРЅРёСЏ
echo "РЈСЃС‚Р°РЅРѕРІРєР° Р·Р°РІРёСЃРёРјРѕСЃС‚РµР№ Python..."
apt-get install -y python3-venv python3-pip
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# РЎРѕР·РґР°РЅРёРµ СЃР»СѓР¶Р±С‹
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
${GREEN}=== РЈРЎРўРђРќРћР’РљРђ Р—РђР’Р•Р РЁР•РќРђ! ===${NC}
${YELLOW}РЎР°Р№С‚ РґРѕСЃС‚СѓРїРµРЅ РїРѕ Р°РґСЂРµСЃСѓ:${NC} http://$(hostname -I | awk '{print $1}'):8000

${YELLOW}Р”Р°РЅРЅС‹Рµ РґР»СЏ РґРѕСЃС‚СѓРїР° Рє Р±Р°Р·Рµ РґР°РЅРЅС‹С…:${NC}
РђРґСЂРµСЃ:   localhost:3306
Р‘Р°Р·Р°:    $MYSQL_DB
Р›РѕРіРёРЅ:   $MYSQL_USER
РџР°СЂРѕР»СЊ:  $MYSQL_PASS

${YELLOW}РљРѕРЅС„РёРіСѓСЂР°С†РёСЏ СЃРѕС…СЂР°РЅРµРЅР° РІ:${NC} $ENV_FILE
${YELLOW}РЈС‡РµС‚РєРё Р‘Р” СЃРѕС…СЂР°РЅРµРЅС‹ РІ:${NC} $CRED_FILE

РџСЂРѕРІРµСЂРёС‚СЊ СЃС‚Р°С‚СѓСЃ: systemctl status boterator
"

