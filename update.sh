#!/bin/bash
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PROJECT_DIR="/opt/boterator"
REPO_URL="https://github.com/kornalexandr2/Boterator.git"

resolve_branch() {
    if git ls-remote --exit-code --heads origin main >/dev/null 2>&1; then
        echo main
        return
    fi
    if git ls-remote --exit-code --heads origin master >/dev/null 2>&1; then
        echo master
        return
    fi
    if git ls-remote --exit-code --heads "$REPO_URL" main >/dev/null 2>&1; then
        echo main
        return
    fi
    echo master
}

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Запустите update.sh через sudo.${NC}"
    exit 1
fi

cd "$PROJECT_DIR"
BRANCH="$(resolve_branch)"

echo "Обновляем Boterator из ветки $BRANCH..."
git fetch origin "$BRANCH"
git pull --ff-only origin "$BRANCH"

source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

systemctl restart boterator.service

echo -e "${GREEN}Обновление завершено успешно.${NC}"
