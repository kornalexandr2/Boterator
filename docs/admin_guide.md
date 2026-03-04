# Руководство Администратора: Установка и настройка Boterator

Данное руководство описывает полный процесс развертывания проекта Boterator, начиная с чистого сервера Ubuntu, заканчивая настройкой Telegram Web App (TWA).

**Важное замечание:** Для работы Telegram Web App (мини-приложения) и вебхуков Telegram **обязательно требуется HTTPS**. Поэтому вам потребуется доменное имя с SSL сертификатом. Для установки скриптом необходима утилита `curl`.

---

## Шаг 1. Подготовка сервера и домена

1. **Требования к серверу:** ОС **Ubuntu 20.04 или 22.04**. Рекомендуемые минимальные характеристики: от 1 CPU, 1 GB RAM, 10 GB SSD.
2. **Требования к домену:** Наличие любого зарегистрированного доменного имени. В панели управления доменом (DNS) должна быть создана A-запись, указывающая на IP-адрес вашего Ubuntu сервера. 
   > *Примечание: Обновление DNS-записей может занять от 15 минут до нескольких часов.*

---

## Шаг 2. Первоначальная настройка сервера

Подключитесь к вашему серверу по SSH:
```bash
ssh root@IP_ВАШЕГО_СЕРВЕРА
```

Обновите пакеты системы и установите базовые утилиты:
```bash
apt update && apt upgrade -y
apt install -y curl git nginx certbot python3-certbot-nginx
```

---

## Шаг 3. Установка Boterator

Проект устанавливается в директорию `/opt/boterator/`. Вы можете выбрать один из вариантов установки:

### Вариант 1: Быстрая установка одной командой (Рекомендуется)
```bash
curl -sSL https://raw.githubusercontent.com/kornalexandr2/Boterator/master/install.sh | sudo bash
```

### Вариант 2: Ручная установка
```bash
sudo mkdir -p /opt/boterator
sudo git clone https://github.com/kornalexandr2/Boterator.git /opt/boterator
cd /opt/boterator
sudo bash install.sh
```

В процессе установки скрипт запросит Bot Token и ваш Telegram ID.

---

## Шаг 4. Настройка Reverse Proxy и получение SSL (HTTPS)

Приложение работает на порту 8000. Для работы Telegram обязательно нужен HTTPS.

### Вариант А: Nginx + Certbot (Терминал)
1. Создайте конфиг `/etc/nginx/sites-available/boterator`:
```nginx
server {
    listen 80;
    server_name bot.yourdomain.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```
2. Активируйте: `ln -s /etc/nginx/sites-available/boterator /etc/nginx/sites-enabled/ && systemctl restart nginx`
3. SSL: `certbot --nginx -d bot.yourdomain.com`

---

## Шаг 5. Настройка .env файла

1. Откройте `/opt/boterator/DEVELOPE/.env`.
2. Установите `APP__BASE_URL=https://bot.yourdomain.com`.
3. Перезапустите: `systemctl restart boterator`.

---

## Шаг 6. Настройка Mini App (TWA) в Telegram

1. Перейдите в **[@BotFather](https://t.me/BotFather)**.
2. Выберите бота -> **Bot Settings** -> **Menu Button** -> **Configure menu button**.
3. Введите URL: `https://bot.yourdomain.com/twa/store` и название `Тарифы`.

---

## Шаг 7. Настройка команд (меню) бота

Чтобы у пользователей была кнопка "Меню":
1. В **[@BotFather](https://t.me/BotFather)** введите `/setcommands`.
2. Выберите бота и отправьте список:
   ```text
   start - Главное меню и витрина тарифов
   ```

---

## Шаг 8. Настройка платежных систем

По умолчанию включен `MOCK_MODE` (тестовый режим).
Для реальных платежей в CRM перейдите во вкладку **Настройки** и выберите **YooMoney** (P2P) или настройте **YooKassa**.

---

## Управление (CRM Администратора)

1. Отправьте боту `/start`.
2. Нажмите кнопку **"⚙️ CRM Администратора"**.
3. В CRM можно управлять тарифами, пользователями, ресурсами и делать рассылки.

## Обновление проекта

```bash
cd /opt/boterator
sudo bash update.sh
```
