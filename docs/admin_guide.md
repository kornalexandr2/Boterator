# Руководство Администратора: Установка и настройка Boterator

Данное руководство описывает полный процесс развертывания проекта Boterator, начиная с чистого сервера Ubuntu, заканчивая настройкой Telegram Web App (TWA).

**Важное замечание:** Для работы Telegram Web App (мини-приложения) и вебхуков Telegram **обязательно требуется HTTPS**. Поэтому вам потребуется доменное имя.

---

## Шаг 1. Подготовка сервера и домена

1. **Аренда сервера:** Арендуйте VPS/VDS сервер с ОС **Ubuntu 20.04 или 22.04**. Рекомендуемые характеристики: от 1 CPU, 1 GB RAM, 10 GB SSD.
2. **Покупка домена:** Купите любое доменное имя (например, на Reg.ru, Beget или Namecheap).
3. **Привязка домена (DNS):** В панели управления доменом создайте A-запись (например, `bot.yourdomain.com`), указывающую на IP-адрес вашего Ubuntu сервера. 
   > *Примечание: Обновление DNS-записей может занять от 15 минут до нескольких часов.*

---

## Шаг 2. Первоначальная настройка сервера

Подключитесь к вашему серверу по SSH:
```bash
ssh root@IP_ВАШЕГО_СЕРВЕРА
```

Обновите пакеты системы:
```bash
apt update && apt upgrade -y
```

Установите необходимые системные утилиты (Git, Nginx, Certbot):
```bash
apt install -y git nginx certbot python3-certbot-nginx
```

---

## Шаг 3. Клонирование и установка Boterator

1. Склонируйте репозиторий проекта в папку `/root/Boterator` (или любую удобную):
```bash
cd /root
git clone https://github.com/ВАШ_ЛОГИН/Boterator.git
cd Boterator
```
*(Замените ссылку на актуальную, если репозиторий приватный, потребуется настроить SSH-ключи или использовать Personal Access Token GitHub).*

2. Запустите инсталлятор:
```bash
sudo bash install.sh
```

В процессе установки скрипт:
- Установит MySQL (если вы выберете авто-настройку) и сгенерирует доступы.
- Запросит `Bot Token` (можно получить у [@BotFather](https://t.me/BotFather)).
- Запросит ваш Telegram ID (чтобы выдать вам права админа). Получить свой ID можно у бота [@getmyid_bot](https://t.me/getmyid_bot).
- Создаст виртуальное окружение, установит зависимости и запустит приложение локально на порту `8000` как системную службу `boterator`.

---

## Шаг 4. Настройка Reverse Proxy и получение SSL (HTTPS)

Приложение теперь работает локально на порту 8000. Для работы Telegram обязательно нужен SSL сертификат (HTTPS). Вы можете выбрать один из двух вариантов настройки: **Вариант А (через консоль Nginx)** или **Вариант Б (через удобный графический Nginx Proxy Manager)**.

### Вариант А: Настройка через классический Nginx и Certbot (через терминал)

1. Установите Nginx и Certbot, если еще не сделали этого:
```bash
apt install -y nginx certbot python3-certbot-nginx
```

2. Создайте конфигурационный файл Nginx для вашего домена:
```bash
nano /etc/nginx/sites-available/boterator
```

3. Вставьте туда следующий код (заменив `bot.yourdomain.com` на ваш реальный домен):
```nginx
server {
    listen 80;
    server_name bot.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```
Сохраните и закройте файл (`Ctrl+O`, `Enter`, `Ctrl+X`).

4. Активируйте конфигурацию и перезапустите Nginx:
```bash
ln -s /etc/nginx/sites-available/boterator /etc/nginx/sites-enabled/
nginx -t
systemctl restart nginx
```

5. Получите бесплатный SSL-сертификат:
```bash
certbot --nginx -d bot.yourdomain.com
```
Следуйте инструкциям на экране. Certbot автоматически обновит конфигурацию Nginx для использования HTTPS.

---

### Вариант Б: Настройка через Nginx Proxy Manager (через браузер)

Если у вас уже установлен Nginx Proxy Manager (NPM) через Docker, этот способ гораздо проще.

1. Убедитесь, что ваш домен (`bot.yourdomain.com`) направлен на IP-адрес вашего сервера в настройках DNS.
2. Откройте панель управления **Nginx Proxy Manager** в вашем браузере (обычно это порт 81, например `http://IP_СЕРВЕРА:81`).
3. Перейдите в раздел **Hosts** -> **Proxy Hosts** и нажмите **Add Proxy Host**.
4. Вкладка **Details**:
   - **Domain Names**: Введите ваш домен (например, `bot.yourdomain.com`) и нажмите Enter.
   - **Scheme**: `http`
   - **Forward Hostname / IP**: Введите IP-адрес вашего сервера (например, `127.0.0.1`, если NPM стоит на том же сервере, но не в Docker, либо внешний IP сервера, если NPM в Docker-контейнере).
   - **Forward Port**: `8000` (порт, на котором работает Boterator).
   - Включите галочки: **Block Common Exploits** и **Websockets Support**.
5. Вкладка **SSL**:
   - В выпадающем списке **SSL Certificate** выберите **"Request a new SSL Certificate"**.
   - Включите галочки: **Force SSL**, **HTTP/2 Support** и **HSTS Enabled**.
   - Введите ваш Email и согласитесь с правилами Let's Encrypt.
6. Нажмите **Save**. NPM автоматически получит сертификат и настроит проксирование.

---

## Шаг 6. Настройка `.env` файла

Теперь, когда у нас есть домен с HTTPS, нужно сообщить приложению его публичный адрес, чтобы оно могло установить Webhook для Telegram.

1. Откройте файл конфигурации:
```bash
nano /root/Boterator/DEVELOPE/.env
```

2. Добавьте или измените строку `APP__BASE_URL`:
```ini
APP__BASE_URL=https://bot.yourdomain.com
```

3. Перезапустите службу Boterator:
```bash
systemctl restart boterator
```
Бот автоматически зарегистрирует свой Webhook в Telegram по адресу `https://bot.yourdomain.com/webhook`.

---

## Шаг 7. Настройка Mini App (TWA) в Telegram

Теперь необходимо привязать созданные веб-страницы к интерфейсу Telegram как Mini Apps.

1. Перейдите в Telegram к боту **[@BotFather](https://t.me/BotFather)**.
2. Выберите вашего бота и перейдите в **Bot Settings** -> **Menu Button**.
3. Нажмите **Configure menu button** и введите URL для клиентской витрины: 
   `https://bot.yourdomain.com/twa/store`
   Назовите кнопку, например, `Тарифы`.
   *Теперь у всех пользователей слева внизу от поля ввода будет кнопка "Тарифы", открывающая витрину.*

*(Для доступа к CRM админа, в текущей реализации бот присылает специальную кнопку по команде `/start` для тех пользователей, чьи ID прописаны в конфигурации).*

---

## Шаг 8. Настройка платежных систем

По умолчанию включен `MOCK_MODE` (режим эмуляции). В этом режиме платежи "успешно проходят" без реального списания денег.

Чтобы включить реальные платежи:
1. Откройте `/root/Boterator/DEVELOPE/.env`.
2. Установите `PAYMENTS__MOCK_MODE=False`.
3. Добавьте ключи вашей платежной системы (например, ЮKassa):
```ini
PAYMENTS__YOOKASSA_SHOP_ID=ваш_shop_id
PAYMENTS__YOOKASSA_SECRET_KEY=ваш_secret_key
```
4. Перезапустите бота: `systemctl restart boterator`

---

## Управление (CRM Администратора)

1. Напишите вашему боту команду `/start`.
2. Так как ваш ID был указан при установке, бот покажет вам кнопку **"⚙️ CRM Администратора"**.
3. Внутри CRM вы можете:
   - Создавать тарифы и делать их скрытыми/видимыми.
   - Просматривать статистику.
   - Делать рассылки по базе пользователей.

## Обновление проекта

Если вышли новые изменения в репозитории на GitHub, для обновления достаточно зайти на сервер и выполнить скрипт обновления. Он подтянет код, обновит библиотеки и перезагрузит службу, **сохранив вашу БД и файл `.env`**.

```bash
cd /root/Boterator
sudo bash update.sh
```