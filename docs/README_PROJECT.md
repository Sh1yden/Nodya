# Nodya

**Персональный ИИ-ассистент с многоканальным доступом, многоуровневой памятью и системой навыков.**

Нодя — это self-hosted AI assistant, который живёт в Telegram, Discord, браузере и CLI. Помнит контекст диалога, извлекает факты в долгосрочную память, умеет выполнять действия через skills и вызывать LLM с fallback-провайдером.

---

## Возможности

- **Многоканальная архитектура** — Telegram (webhook), Discord, REST API (browser), CLI
- **Многоуровневая память**:
  - Short-term (Redis) — контекст диалога, состояния, блокировки
  - Long-term (PostgreSQL) — пользователи, факты, аудит
  - Vector (Qdrant) — семантический поиск для RAG
- **LLM с провайдерами** — Google AI Studio (Gemini) как primary, OpenRouter как fallback
- **Система навыков (skills)** с 4 уровнями доступа (safe/elevated/sandboxed/system)
- **Multi-tenant** — один инстанс для команды, роли owner/user
- **Nodya Agent** — отдельный процесс для host-level операций (опционально)
- **Асинхронность** — полный async-стек (FastAPI, SQLAlchemy async, RabbitMQ, Redis)

---

## Технологии

FastAPI · SQLAlchemy (async) · PostgreSQL 16 · Redis 8 · RabbitMQ · Qdrant
Google AI Studio (Gemini) via Cloudflare Worker · OpenRouter · Alembic · Argon2id · APScheduler
Python 3.13 · Docker Compose · httpx

---

## Быстрый старт

### 1. Предварительные требования

- Docker и Docker Compose
- Python 3.13+
- UV (менеджер пакетов)

### 2. Клонирование и настройка

```bash
git clone https://github.com/Sh1yden/Nodya.git
cd Nodya
cp .env.example .env
```

Отредактируйте `.env`, указав свои ключи:
- `TELEGRAM_BOT_TOKEN` — токен Telegram-бота
- `TELEGRAM_WEBHOOK_SECRET` — секрет вебхука (любая длинная строка)
- `GEMINI_API_KEY` — ключ Google AI Studio
- `GEMINI_CLOUDFLARE_URL` — **обязателен**, URL твоего Cloudflare Worker (без дефолта, не используй чужой)
- `OPENROUTER_API_KEY` — ключ OpenRouter для fallback
- `TELEGRAM_WEBHOOK_SECRET` — уже выше
- Owner создаётся отдельно: `uv run python -m app.brain.bootstrap --username <u> --password <p>` (не через `.env`)

Про `TELEGRAM_WEBHOOK_URL`:
- **Пусто** (локальная разработка) — приложение само поднимет
  cloudflared quick-tunnel и повесит вебхук на него.
  Требует установленного бинарника `cloudflared` на хосте.
- **Задан** — используется как есть. Единственный вариант внутри Docker
  (бинарников туннелей в контейнере нет).

### 3. Запуск (разработка)

```bash
# Виртуальное окружение
uv venv
source .venv/bin/activate
uv sync

# Миграции
alembic upgrade head

# Создать owner (один раз, после первого деплоя)
uv run python -m app.brain.bootstrap --username Shayden --password <pwd>

# Запуск (FastAPI + Worker в одном процессе)
uv run uvicorn app.main:app --reload
```

### 4. Запуск (Docker Compose)

```bash
docker-compose up -d
```

Поднимет: `app` (FastAPI + Worker), `postgres`, `redis`, `rabbitmq`, `qdrant`.

---

## Структура проекта

```
├── app/                    # Nodya Core — основной сервер
│   ├── main.py             # Точка входа: Gateway + фоновые задачи
│   ├── worker.py           # Главный обработчик сообщений
│   ├── core/               # Конфиг, логгер
│   ├── api/                # auth, health, messaging, туннели
│   ├── chats/              # Каналы: telegram/ (webhook + sender)
│   ├── brain/              # "Мозг": модели, память, LLM, скиллы
│   └── common/             # Общие DTO и топология брокера
├── agent/                  # Nodya Agent (опционально) — host-level скиллы
├── shared/                 # Общие схемы для RPC Core ↔ Agent
├── docs/                   # Документация
├── tests/                  # Тесты
├── docker-compose.yml
└── Dockerfile
```

---

## Архитектура

Основной поток сообщения:

```
Telegram ──webhook──> FastAPI ──publish──> RabbitMQ ──consume──> Worker
                                                                   │
                                              ┌────────────────────┘
                                              ▼
                                   ┌──────────────────┐
                                   │  Context Assembly │ (Redis + PG + Qdrant)
                                   └───────┬──────────┘
                                           ▼
                                   ┌──────────────────┐
                                   │  LLM (Gemini)    │ ←→ SkillRegistry
                                   └───────┬──────────┘
                                           ▼
                                   ┌──────────────────┐
                                   │  OutgoingMessage  │
                                   └───────┬──────────┘
                                           ▼
                                    RabbitMQ ────> TG Sender ────> Telegram
```

Детальное описание — в `docs/ARCHITECTURE_FULL.md`.

---

## Разработка

```bash
# Миграции
alembic revision --autogenerate -m "description"
alembic upgrade head

# Линтер
ruff check .
ruff format .

# Тесты
pytest -m "not integration"  # unit, без БД
pytest                      # все (нужен docker-compose up postgres/redis)

# Стиль кода — см. docs/ARCHITECTURE_FULL.md §9
```

---

## Лицензия

MIT
