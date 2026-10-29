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
cp observability/grafana.env.example observability/grafana.env
# задайте GF_SECURITY_ADMIN_PASSWORD, остальное можно оставить как есть

docker compose up -d --build
```

Поднимет:

- `app` — FastAPI + Worker (`nodya_heart`, host-порт `127.0.0.1:8014`);
- `postgres`, `redis`, `rabbitmq`, `qdrant`;
- `admin` — read-only панель (`nodya_admin`, без host-портов, доступ
  только через tunnel);
- `docker-proxy` — socket-proxy с `POST=0` для admin/alloy;
- стек observability: `prometheus`, `loki`, `alloy`, `cadvisor`,
  `redis-exporter`, `postgres-exporter`, `grafana` (без host-портов).

Все контейнеры пишут docker-логи с ротацией (`json-file`, 10 MB × 3),
приложение — JSONL в `./logs` (14 дней). Метрики приложения отдаются на
внутреннем порту `8015` (не публикуется).

### 5. Админ-панель и Grafana

Ingress в cloudflared BridgeNode:

```yaml
- hostname: admin.nodya.shayden.ru
  service: http://nodya_admin:8016
- hostname: grafana.nodya.shayden.ru
  service: http://nodya_grafana:3002
```

- `admin.nodya.shayden.ru` — owner-логин (тот же пользователь, что
  bootstrap; роль должна быть `owner`). Подробности — `docs/ADMIN_PANEL.md`.
- `grafana.nodya.shayden.ru` — Cloudflare Access (owner-email) + логин
  Grafana из `observability/grafana.env`. Дашборды и метрики —
  `docs/OBSERVABILITY.md`.

Локальная разработка панели без HTTPS:

```dotenv
ADMIN_COOKIE_SECURE=false
ADMIN_PUBLIC_ORIGIN=http://localhost:8016
ADMIN_ALLOWED_HOSTS=localhost,127.0.0.1
```

иначе браузер выбросит `Secure`-cookie и вход зациклится.

То же самое для контейнеров — опциональный override
`docker-compose.local.yml` публикует admin и Grafana только на
`127.0.0.1`:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d admin grafana
```

- панель: `http://localhost:8016`
- Grafana: `http://localhost:3002` (логин из `observability/grafana.env`)

Возврат к боевому режиму (без host-портов, `Secure`-cookie):
`docker compose up -d admin grafana`.

---

## Структура проекта

```
├── app/                    # Nodya Core — основной сервер
│   ├── main.py             # Точка входа: Gateway + фоновые задачи
│   ├── worker.py           # Главный обработчик сообщений
│   ├── core/               # Конфиг, логгер, метрики, error sink
│   ├── api/                # auth, health, messaging, туннели
│   ├── chats/              # Каналы: telegram/ (webhook + sender)
│   ├── brain/              # "Мозг": модели, память, LLM, скиллы
│   ├── admin/              # Read-only панель мониторинга (отдельный процесс)
│   └── common/             # Общие DTO и топология брокера
├── agent/                  # Nodya Agent (опционально) — host-level скиллы
├── shared/                 # Общие схемы для RPC Core ↔ Agent
├── observability/          # Prometheus, Loki, Alloy, Grafana provisioning
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
