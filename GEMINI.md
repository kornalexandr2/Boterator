# Boterator (Telegram-бот + TWA) - GEMINI.md

## Project Overview
**Boterator** is a Telegram bot integrated with a Telegram Web App (TWA) designed to manage paid subscriptions in Telegram channels and groups. It handles automated join request processing, payment integrations, content protection, and provides a CRM dashboard for administrators.

### Core Technologies
- **Backend:** Python, FastAPI (API, Webhooks, Jinja2 rendering)
- **Telegram Bot:** Aiogram 3.x
- **Database:** MySQL (Asynchronous via `aiomysql` and `SQLAlchemy`)
- **Frontend (TWA):** Jinja2 templates + Plain JavaScript/HTML/CSS (no heavy frameworks)
- **Deployment:** systemd service on Ubuntu (no Docker)

### Architecture
- **FastAPI:** Acts as the backend core, providing API endpoints, handling webhooks, and rendering TWA interfaces.
- **Aiogram:** Manages the Telegram bot logic, including processing join requests, broadcasting messages, and subscription monitoring.
- **Graceful Degradation:** The application MUST start even if database connections or API keys are missing, logging warnings instead of crashing.
- **Logging:** Structured logging (INFO, WARNING, ERROR/CRITICAL) with file rotation.

## Building and Running
The project uses automated bash scripts for installation and updates.

- **Installation:** `bash install.sh`
  - Creates virtual environment and installs dependencies from `requirements.txt`.
  - Interactively requests configuration (bot token, MySQL credentials).
  - Generates and activates a `systemd` service file.
- **Updating:** `bash update.sh`
  - Pulls latest changes from GitHub (`origin main`).
  - Updates dependencies and restarts the service while preserving configurations in `DEVELOPE/`.
- **Requirements:** To be documented in `requirements.txt`.

## Development Conventions
- **No Stubs:** Never use `pass`, `...`, or `TODO`. Implement complete, functional code for every module.
- **Database:** Only use asynchronous MySQL. SQLite is strictly forbidden.
- **Security:**
  - Never hardcode secrets, tokens, or passwords.
  - The `DEVELOPE/` directory is private and MUST be ignored by Git.
- **Documentation:**
  - `DEVELOPE/dev_docs.md`: Private developer documentation.
  - `docs/admin_guide.md`: Public administrator guide.
  - `docs/user_guide.md`: Public user guide.
- **Git Flow:** All commit messages MUST be in Russian.

## Key Files & Directories
- `DEVELOPE/boterator-tz.txt`: The primary technical specification (TZ).
- `DEVELOPE/config.yaml` / `.env`: Private configuration files (not in Git).
- `.env.example` / `config.example.yaml`: Templates for configuration.
- `install.sh` / `update.sh`: Deployment and maintenance scripts.
- `requirements.txt`: Python package dependencies.
