# Nodya — Master TODO

> Полный перечень работ: от фикса багов до Nodya Agent.
> Этапы выполняются последовательно. Каждый этап должен быть полностью завершён (все пункты) перед переходом к следующему.

---

## Поправки (2026-08-24) — зафиксированные архитектурные решения

Развилки исходного плана закрыты, решения обязательны к реализации:

| # | Решение | Содержание |
|---|---|---|
| 1 | Debounce в Worker | Gateway публикует каждое сообщение сразу, без Redis. Worker держит фиксированное окно 5с (`DEBOUNCE_SECONDS`) с момента первого сообщения, копит пачку в памяти процесса. Крэш → сообщения не были ACK → redelivery, at-least-once сохраняется |
| 2 | Отложенные сообщения | Redis ZSet `nodya:scheduled` (score = unix ts отправки) + APScheduler-job каждые ~30s (`SCHEDULED_POLL_SECONDS`) переносит созревшее в `outgoing_messages`. RabbitMQ per-message TTL отклонён (head-of-line blocking) |
| 3 | Токены доступа | Opaque-токены хэшируются **SHA-256** (высокоэнтропийный токен не требует медленного KDF). Argon2id — только для паролей. См. обновлённый ADR-3 |
| 4 | Занятый лок | Не NACK+requeue (горячий цикл). ACK исходных сообщений + запись пачки в `nodya:scheduled` (+30s) с `retry_count`; после 5 попыток (`MAX_SCHEDULED_RETRIES`) → DLQ + лог ERROR |
| 5 | Webhook Telegram | Обязательная проверка заголовка `X-Telegram-Bot-Api-Secret-Token` (`secrets.compare_digest`); секрет в `set_webhook` и в Settings |
| 6 | Новая таблица `messages` | Сырой архив входящих/исходящих; consolidation НЕ чистит. Защита от потери истории |
| 7 | Новая таблица `feed_sources` | Источники RSS/TG фонового парсера (Этап 7) |
| 8 | OWNER | `OWNER_USERNAME` (в модели `Users` нет поля email — вариант с OWNER_EMAIL невозможен без миграции) |
| 9 | Туннели для dev | Без `TELEGRAM_WEBHOOK_URL` локально поднимается cloudflared quick-tunnel. Tuna отклонена (RU-сегмент, обход не тянет). Внутри Docker бинарника нет — там обязателен явный URL. Stdout туннеля дренируется фоновым потоком (иначе пайп переполняется и туннель замирает) |

Порядок реализации: E(docs) → B(инфра) → C(вертикальный срез TG→эхо) → D1..D5 (бывшие Этапы 3–9).

## Журнал выполнения

| Дата | Инкремент | Результат |
|---|---|---|
| 2026-08-24 | E | Решения запечатаны в docs |
| 2026-08-24 | B | Redis noeviction+AOF, healthcheck Qdrant, внешняя сеть, .env.example |
| 2026-08-24 | C | Вертикальный срез работает E2E (проверено на живом TG): webhook+секрет → RabbitMQ → Worker(debounce 5с, лок, scheduled-poller, авторегистрация TG) → эхо → TGSender; туннели cloudflared/tuna; fail-fast + /health. Попутно исправлен vhost-баг в rabbitmq_url |
| 2026-08-24 | D1 | Модель Messages (+миграция eda380bd303f), sha256-токены (ADR-3), /auth/register + /auth/login с ролями owner (проверено curl: 201/409/401/200), deps.get_current_user, архивация пачек в messages из Worker (degraded при сбое). Найдено и исправлено: дрейф схемы старой БД (пересоздан volume postgres), отсутствие relationship Users↔AuthTokens ломало порядок INSERT |
| 2026-08-24 | H | Tuna удалена (RU-сегмент); drain stdout cloudflared (фикс зависания туннеля); drop_pending_updates=False на set_webhook (офлайн-сообщения доставляются); подробные логи webhook/worker/sender; FK ON DELETE CASCADE (миграция abbdbc96f127) + DELETE /auth/me (204/401 проверено curl) |
| 2026-08-24 | N | Интеграция с named-tunnel инфраструктурой: ingress nodya.shayden.ru → nodya_heart:8014 в BridgeNode/config.yml; internet_bridge возвращён в compose (конвенция инфраструктуры — ошибка части B признана); Dockerfile --no-install-project (hatch-vcs без .git); полный стек в контейнерах, health 200 через https://nodya.shayden.ru. Quick-tunnel оставлен как zero-config fallback |
| 2026-08-24 | D2 | Память ожила: push_context_many (пайплайн), state-машина thinking/idle с TTL, proactive_decision-заглушка («now»), get_db/repo-типы. Верифицировано: пачка n=3 одним батчем, 6 записей контекста TTL 24ч, state=idle |
| 2026-08-24 | D3 | LLM-слой по матрице tldr: D = gemini-3.5-flash-lite → 3.1-flash-lite → OR nemotron ultra/super:free (CS=3.6-flash и BP=gemma/nemotron прописаны, подключаются в Этапе 7); VS=gemini-embedding-2 без OR-fallback (embeddings у OpenRouter нет). Провайдеры gemini/openrouter + роутер с transient/fatal-разделением и backoff. Worker: системный промпт (дефолт ME/RULES + переопределение файлами) + история Redis → один ответ на пачку. Fail-fast ключей возвращён. Живо проверено: ответ Gemini + кросс-провайдерный fallback на nemotron |
| 2026-08-25 | R1 | Рефакторинг структуры: app/chats/<канал>/ объединяет webhook+sender (senders/ упразднён); prompts.py → brain/memory/init/ с cwd-независимым путём; main.py → app/main.py (+CMD Dockerfile); фасады __init__ во всех пакетах, внешние импорты только через корни; CI: +docker-build job (build-only, gha-cache), concurrency, workflow_dispatch |
| 2026-08-25 | R2 | Англофикация кода: все комментарии/docstrings → EN (Google-style на каждой функции), кириллица в py-файлах = 0 (кроме намеренных дефолтов промптов); логи переведены на f-строки и политику уровней (DEBUG=поток отладки, INFO=пользователь, WARN=непредвиденное, ERROR=деградация, CRITICAL=падение); добавлены DEBUG-крошки (broker connect, state, poller, archive); Better Comments маркеры TODO/!/?. Доки остаются RU |
| 2026-08-25 | D4 | Долгосрочная память: VectorMemory (Qdrant, ленивая коллекция с авто-dim, payload-фильтр user_id); миграция 06cbb2bf0b3e UNIQUE(user_id,category,key) + upsert_fact ON CONFLICT RETURNING; ConsolidationJob v2 — ОДИН вызов CS-модели даёт facts+summary, атомарный replace_context кладёт саммари ролью "summary" (доки §4.3 уточнены: компрессия вместо голой очистки, обязанность CS из §5.4 операционализирована); APScheduler скан 30 мин / молчание ≥3ч; Worker: топ-20 фактов + 5 семантических хитов в промпт с дедупом и confidence≥0.4; ручной прогон python -m app.brain.memory.consolidation |
| 2026-08-26 | L1 | Связывание Telegram↔аккаунт: POST /auth/telegram/code (Bearer, одноразовый код GETDEL, TTL 10 мин, алфавит без 0/O/1/I); Worker перехватывает /link до debounce/LLM; мёрж двойника = UPDATE messages/hard_facts → освобождение tg_id → delete dup (порядок против unique-конфликта) → RENAME Redis-ключей → reassign Qdrant payload (фильтр через points=Filter — сигнатура клиента); HTTPBearer в deps → кнопка Authorize в Swagger; частичные сбои переноса репортятся честно. Мёрж владельца выполнен и проверен живьём (47 сообщений, факт, саммари, точка Qdrant под owner) |
| 2026-08-27 | D5 | Тесты готовы. |
| 2026-08-30 | R3 | Рефакторинг LLM-провайдеров: добавлен ProviderRegistry (ленивая инициализация, конфиг-цепочки), заменён прямой Gemini на GeminiCloudflareProvider (httpx → Cloudflare Worker https://geminifix.shayden.workers.dev/), старый GeminiProvider отключён через GEMINI_ENABLED=false. Роутер теперь строит цепочки из настроек LLM_PROVIDER_CHAINS. Обновлены тесты и доки. |

Следующий шаг: D4-проактивность (70/20/10, RSS+feed_sources) или browser-канал; затем D5 тесты.
---

## **Этап 0: - [x] Фикс того, что уже написано**

Блокирующие баги в существующем коде. Без этого ни миграции, ни регистрация, ни запуск не работают.

### 0.1 - [x] `app/core/__init__.py` — опечатка `all`
- **Файл:** `app/core/__init__.py`
- **Что сделать:** Заменить `all = [...]` на `__all__ = [...]`
- **Затрагивает:** импорт `from app.core import *`
- **Приоритет:** высокий (код не работает как задумано)

### 0.2 - [x] `app/core/logger.py` — `root_prefix = "syncnode"`
- **Файл:** `app/core/logger.py`, строка 19
- **Что сделать:** Заменить `"syncnode"` на `"nodya"`
- **Вариант:** Вынести в `SettingsSchema` как `LOG_PREFIX`
- **Приоритет:** средний (косметика, но ломает namespace логов)

### 0.3 - [x] Модель `Users` — убрать `browser_id`, `cli_id`
- **Файл:** `app/brain/models/Users.py`
- **Проблема:** `browser_id: Mapped[UUID]` и `cli_id: Mapped[UUID]` — self-referencing FK с `NOT NULL`. Нельзя вставить первого пользователя
- **Что сделать:**
  1. Удалить поля `browser_id`, `cli_id`
  2. Добавить поля (если ещё нет): `created_at`
  3. Убедиться, что `passwd_hash` существует (уже есть)
- **Текущее состояние Users:** `user_id`, `telegram_id`, `discord_id`, `username`, `passwd_hash`, `role`, `settings`, ~~`browser_id`~~, ~~`cli_id`__
- **Добавить:** `created_at: Mapped[datetime]`

### 0.4 - [x] Модель `AuthTokens` — завершить поля
- **Файл:** `app/brain/models/AuthTokens.py`
- **Проблема:** `token_hash`, `created_at`, `last_used_at`, `revoked_at` закомментированы
- **Что сделать:** Раскомментировать и реализовать:
  ```python
  token_hash: Mapped[str] = mapped_column(String, nullable=False)
  created_at: Mapped[datetime] = mapped_column(
      DateTime(timezone=True), server_default=func.now(), nullable=False
  )
  last_used_at: Mapped[datetime | None] = mapped_column(
      DateTime(timezone=True), nullable=True
  )
  revoked_at: Mapped[datetime | None] = mapped_column(
      DateTime(timezone=True), nullable=True
  )
  ```
- **Импорт:** datetime, func из sqlalchemy

### 0.5 - [x] `AuthTokens` не импортируется в `models/__init__.py`
- **Файл:** `app/brain/models/__init__.py`
- **Что сделать:** Добавить `AuthTokens` в `__all__` и импорт
- **Текущий список:** `["Base", "Users", "HardFacts", "AuditLogs"]` -> `["Base", "Users", "AuthTokens", "HardFacts", "AuditLogs"]`

### 0.6 - [x] `HardFactsRepo.search_last_updated` — пустой стаб
- **Файл:** `app/brain/repositories/HardFactsRepo.py`
- **Что сделать:** Реализовать метод:
  ```python
  async def search_last_updated(self, user_id: UUID, limit: int = 20) -> list[HardFacts]:
      stmt = (
          select(HardFacts)
          .where(HardFacts.user_id == user_id)
          .order_by(HardFacts.updated_at.desc())
          .limit(limit)
      )
      result = await self.session.scalars(stmt)
      return list(result.all())
  ```
- **Добавить импорты:** `select` из sqlalchemy, `UUID` из uuid

### 0.7 - [x] Пересоздать init-миграцию
- Старая миграция (`438cf681b3d4`) содержит неправильную схему (с `browser_id`, `cli_id`)
- **Что сделать:**
  1. Удалить или откатить старую миграцию
  2. Создать новую: `alembic revision --autogenerate -m "init_schema"`
  3. Проверить, что `upgrade()` создаёт правильные таблицы (Users без browser_id/cli_id, AuthTokens с правильными полями)

### 0.8 - [x] `app/brain/memory/short/redis.py` — пустой класс
- Реализацию отложить на Этап 2, но файл уже существует — убрать заглушку или задепрекейтить

---

## **Этап 1: - [x] Инфраструктурный фундамент**

Dockerfile, docker-compose, pyproject.toml — всё, что нужно для запуска.

### 1.1 - [x] Dockerfile
- **Файл:** `Dockerfile` (сейчас пустой)
- **Что сделать:**
  ```dockerfile
  FROM python:3.13-slim AS builder
  COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
  WORKDIR /app
  COPY pyproject.toml uv.lock ./
  RUN uv sync --no-dev --frozen
  
  FROM python:3.13-slim
  WORKDIR /app
  COPY --from=builder /app/.venv .venv
  COPY . .
  CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8014"]
  ```
- **Образ:** мультистейдж, UV для зависимостей, минимальный финальный слой

### 1.2 - [x] docker-compose — добавить RabbitMQ
- **Файл:** `docker-compose.yml`
- **Что добавить:**
  ```yaml
  rabbitmq:
    container_name: nodya_broker
    image: rabbitmq:4-alpine
    restart: unless-stopped
    environment:
      RABBITMQ_DEFAULT_USER: ${RABBITMQ_USER:-guest}
      RABBITMQ_DEFAULT_PASS: ${RABBITMQ_PASSWORD:-guest}
    ports:
      - "${RABBITMQ_PORT:-5672}:5672"
      - "15672:15672"  # Management UI (non-essential)
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "check_port_connectivity"]
      interval: 10s
      timeout: 5s
      retries: 5
    volumes:
      - rabbitmq_data:/var/lib/rabbitmq
  ```

### 1.3 - [x] docker-compose — добавить Qdrant
- **Файл:** `docker-compose.yml`
- **Что добавить:**
  ```yaml
  qdrant:
    container_name: nodya_vector
    image: qdrant/qdrant:latest
    restart: unless-stopped
    ports:
      - "${QDRANT_PORT:-6333}:6333"
    volumes:
      - qdrant_data:/qdrant/storage
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/healthz"]
      interval: 10s
      timeout: 5s
      retries: 5
  ```
- **Добавить volumes:** `rabbitmq_data:`, `qdrant_data:`
- **Добавить depends_on в app:** `rabbitmq`, `qdrant`

### 1.4 - [x] pyproject.toml — добавить зависимости
- **Файл:** `pyproject.toml`
- **Что добавить в `dependencies`:**
  ```
  fastapi>=0.140.0
  uvicorn>=0.51.0
  httpx>=0.28.0
  qdrant-client>=1.13.0
  google-genai>=1.0.0         # или google-generativeai
  apscheduler>=3.11.0
  ```
- **Добавить в `[project.optional-dependencies]` или отдельно:**
  ```
  dev = ["pytest>=8.0", "pytest-asyncio>=0.25", "ruff>=0.9", "pytest-cov>=6.0"]
  ```

### 1.5 - [x] `SettingsSchema` — добавить недостающие поля
- **Файл:** `app/core/config.py`
- **Что добавить:**
  - `RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`
  - `QDRANT_HOST`, `QDRANT_PORT`
  - `GEMINI_API_KEY`, `OPENROUTER_API_KEY`
  - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_URL`
  - `SYSTEM_SKILLS_ENABLED`, `SANDBOX_ENABLED`
  - `OWNER_EMAIL`
  - `computed_field` для `rabbitmq_url`

### 1.6 - [x] `.env.example` — актуализировать
- **Файл:** `.env.example`
- **Что сделать:** Добавить все недостающие переменные с комментариями

---

## **Этап 2: - [x] Redis-слой памяти (short-term)**

### 2.1 `app/brain/memory/short/redis.py` — `RedisClient`

Полная реализация класса. Наследуется от `redis.asyncio.Redis` (но лучше через композицию, не наследование — Redis уже имеет кучу своих методов).

**Структура:**
```python
# Ключи в Redis:
# nodya:state:{user_id}         -> Hash   (status, last_active_at)
# nodya:context:{user_id}       -> Capped List (LTRIM + TTL)
# nodya:lock:{user_id}          -> String (SET NX EX, value=owner_token)
# nodya:debounce:{user_id}      -> List
# nodya:agent_online:{user_id}   -> String (SET with TTL 15s)


class RedisClient:
    def __init__(self, redis_url: str):
        self._redis = Redis.from_url(redis_url, db=0)

    async def get_state(self, user_id: UUID) -> UserState | None:
        """Читает Hash nodya:state:{user_id}.
        Возвращает None если ключа нет."""

    async def set_state(
        self, user_id: UUID, status: str, ttl: int | None = None
    ) -> None:
        """Пишет status в Hash.
        Если ttl указан — устанавливает TTL на ключ."""

    async def get_state_field(self, user_id: UUID, field: str) -> str | None:
        """Получить одно поле из state Hash."""

    async def push_context(self, user_id: UUID, message: ContextMessage) -> None:
        """LPUSH + LTRIM (до N=100) + EXPIRE (24ч)."""

    async def get_context(self, user_id: UUID, limit: int = 20) -> list[ContextMessage]:
        """LRANGE 0 limit-1, вернуть в хронологическом порядке."""

    async def clear_context(self, user_id: UUID) -> None:
        """DELETE ключа контекста."""

    async def acquire_lock(self, user_id: UUID, ttl: int = 30) -> bool:
        """SET NX EX. Возвращает True/False."""

    async def release_lock(self, user_id: UUID) -> None:
        """Снимает лок (через Lua-скрипт или проверку владельца)."""

    async def is_locked(self, user_id: UUID) -> bool:
        """EXISTS ключа блокировки."""

    async def push_debounce(self, user_id: UUID, text: str) -> int:
        """Добавить в буфер debounce, вернуть размер буфера."""

    async def pop_debounce_batch(self, user_id: UUID) -> list[str]:
        """Атомарно: LRANGE + DELETE. Вернуть всё."""

    async def set_agent_online(self, user_id: UUID, ttl: int = 15) -> None:
        """SET nodya:agent_online:{user_id} 1 EX {ttl}."""

    async def is_agent_online(self, user_id: UUID) -> bool:
        """EXISTS nodya:agent_online:{user_id}."""

    async def close(self) -> None:
        await self._redis.close()
```

---

## **Этап 3: API Gateway (FastAPI)**

### 3.1 `app/api/__init__.py`
- Создать пакет

### 3.2 `app/api/deps.py`
- `async def get_current_user(token: str = Header(..., alias="Authorization")) -> Users`:
  - Извлекает Bearer-токен
  - Хэширует SHA-256 (не argon2id — поправка 3, ADR-3)
  - Ищет в `AuthTokens` по хэшу
  - Проверяет `revoked_at is NULL`
  - Возвращает `Users`
  - 401 если не найден/отозван

### 3.3 `app/api/health.py`
- `GET /health` -> `HealthStatus`:
  - Pings PostgreSQL, Redis, RabbitMQ, Qdrant параллельно (`asyncio.gather`)
  - `HealthStatus` = `{postgres: bool, redis: bool, rabbitmq: bool, qdrant: bool}`
  - Не роняет процесс при частичном отказе
  - Используется при bootstrap для fail-fast

### 3.4 `app/api/auth/routes.py`
- **POST /auth/register:**
  - `RegisterRequest`: `{username: str, email: str | None, password: str}`
  - Проверка уникальности username
  - `hash_password(password)` -> argon2id-хэш
  - Создание `Users`:
    - Если это первый пользователь ИЛИ email совпадает с `OWNER_EMAIL` -> `role="owner"`
    - Иначе `role="user"`
   - Создание `AuthTokens`: `token_hash` = sha256(token)
   - Ответ: `{user_id, token}` (plaintext)
- **POST /auth/login:**
  - `LoginRequest`: `{username: str, password: str}`
  - `verify_password(password, stored_hash)`
  - Генерация нового opaque-токена -> `AuthTokens` (`token_hash` = sha256(token))
  - Ответ: `{token}` (plaintext)

### 3.5 `app/api/chats/tg/` — Telegram (только incoming)
- **Webhook:**
  - `POST /webhook/telegram` — стандартный endpoint от Telegram Bot API
  - Обязательная проверка заголовка
    `X-Telegram-Bot-Api-Secret-Token` против `TELEGRAM_WEBHOOK_SECRET`
    через `secrets.compare_digest`; не совпал -> 403 (поправка 5)
  - Pydantic-модель `TelegramUpdate` (update_id, message, etc.)
  - Валидация через `aiogram.types.Update` или свою модель
  - Извлечение `telegram_id` и текста
  - `publish_incoming(IncomingMessage(channel="telegram", ...))`
  - Ответ `202 Accepted`
- **API Gateway НЕ отправляет сообщения.** Отправка — в `app/senders/tg_sender.py`

### 3.6 `app/api/chats/browser/` — Browser (REST + WebSocket, только incoming)

- **POST /api/chats/browser/send:**
  - Требует `Authorization: Bearer <token>`
  - `SendRequest`: `{text: str}`
  - `publish_incoming(IncomingMessage(channel="browser", user_external_id=str(user.user_id), ...))`
  - Ответ `202 Accepted`
- **WebSocket endpoint:**
  - `GET /ws?token=<auth_token>` — апгрейд до WebSocket
  - Аутентификация: хэш токена, поиск в `AuthTokens` (как в `get_current_user`)
  - Регистрация сокета в `WebSocketManager`
  - Приём сообщений от клиента -> `publish_incoming`
  - Consumer `outgoing_messages` для browser -> `WebSocketManager.send_to_user()`

### 3.7 `app/api/ws.py` — `WebSocketManager`
```python
class WebSocketManager:
    """Менеджер WebSocket-соединений.

    Хранит мапу user_id -> list[WebSocket].
    Позволяет отправлять проактивные сообщения от Ноди клиенту.
    """

    def __init__(self):
        self._connections: dict[UUID, list[WebSocket]] = {}

    async def connect(self, user_id: UUID, ws: WebSocket) -> None:
        """Принять соединение, добавить в мапу."""

    async def disconnect(self, user_id: UUID, ws: WebSocket) -> None:
        """Удалить сокет из мапы. Если список пуст — удалить ключ."""

    async def send_to_user(self, user_id: UUID, message: str | dict) -> None:
        """Отправить сообщение через ВСЕ открытые сокеты пользователя."""

    async def send_to_one(
        self, user_id: UUID, ws: WebSocket, message: str | dict
    ) -> None:
        """Отправить сообщение через конкретный сокет."""

    async def broadcast(self, message: str | dict) -> None:
        """Отправить всем подключённым клиентам (осторожно, только для owner?)."""

    async def close_all(self) -> None:
        """Закрыть все соединения (graceful shutdown)."""
```

### 3.8 `app/api/messaging.py` — `MessagePublisher`
```python
class MessagePublisher:
    def __init__(self, rabbitmq_url: str):
        self._url = rabbitmq_url
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None
        self._exchange: Exchange | None = None
    
    async def connect(self):
        """Подключение к RabbitMQ, декларация exchange/queues."""
    
    async def publish_incoming(self, message: IncomingMessage) -> None:
        """publish в exchange='nodya', routing_key='incoming'."""
    
    async def publish_outgoing(self, message: OutgoingMessage) -> None:
        """publish в exchange='nodya', routing_key='outgoing'."""
    
    async def close(self):
        """Закрыть соединение."""
```

---

## **Этап 4: Worker — основной процесс (отдельно от API Gateway)**

Worker — независимый процесс. Не имеет доступа к HTTP, не знает о WebSocket. Взаимодействует только через RabbitMQ + БД.

### 4.1 `app/common/schemas.py`
```python
# Все общие типы
class IncomingMessage(BaseModel):
    user_external_id: str
    channel: Literal["telegram", "discord", "browser", "cli"]
    text: str
    received_at: datetime


class OutgoingMessage(BaseModel):
    user_id: UUID
    channel: str
    text: str
    delay_until: datetime | None = None  # для отложенных ответов (proactive decision)


class UserState(BaseModel):
    status: Literal["idle", "thinking", "sleeping"]
    last_active_at: datetime


class ContextMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    timestamp: datetime


class PromptContext(BaseModel):
    history: list[ContextMessage]
    facts: list[HardFacts]
    vector_hits: list[VectorHit]


class LLMResponse(BaseModel):
    text: str | None
    tool_calls: list[ToolCall]


class ToolCall(BaseModel):
    name: str
    arguments: dict


class SkillResult(BaseModel):
    success: bool
    payload: Any
    error: str | None
```

### 4.2 `app/worker.py` — `class Worker`
```python
class Worker:
    def __init__(self, redis: RedisClient, db_session_factory, publisher, registry, llm_router, ...):
        self._consumer_tag: str | None = None
        self._running = False
    
    async def run(self):
        """Подключиться к RabbitMQ, начать consume incoming_messages."""
    
    async def stop(self):
        """Отписаться от очереди, дождаться завершения текущей задачи."""
    
    async def handle_message(self, message: IncomingMessage) -> None:
        """
        1. resolve_user(message.channel, message.user_external_id)
        2. Debounce: фиксированное окно DEBOUNCE_SECONDS с момента
           первого сообщения; пачка копится в памяти Worker'а
           (поправки 1). Сообщения не ACK до обработки пачки.
        3. acquire_lock(user.user_id); если лок занят — ACK исходных
           + запись пачки в nodya:scheduled (+30s) с retry_count;
           retry_count >= MAX_SCHEDULED_RETRIES -> DLQ (поправка 4)
        4. set_state(user.user_id, "thinking")
        5. build_context(user.user_id, debounce_texts)
        6. assemble_system_prompt(user, context)
        7. proactive_decision() — ответить/отложить/пропустить (рандом <= 2ч)
        8. Если решила ответить -> LLM generate
        9. tool_calls loop
        10. push_context(user.user_id, ...)
        11. publish outgoing (с delay_until если отложенный)
        12. release_lock(user.user_id)
        13. ACK всех сообщений пачки
        """
    
    async def proactive_decision(self, user: Users) -> ProactiveDecision:
        """Принимает решение: ответить сейчас, отложить или пропустить.
        
        Логика:
        - Проверка state (не занята ли другим процессом)
        - Если "не в настроении" — рандомное решение (подкинуть монетку)
        - Если отложить — рандомное время от 5 мин до 2 часов
        - Возвращает ProactiveDecision {action, delay_seconds}
        """
    
    async def resolve_user(self, channel: str, external_id: str) -> Users:
        """Поиск по telegram_id/discord_id. Если не найден — создание (только для TG/DS)."""
    
    async def build_context(self, user_id: UUID) -> PromptContext:
        """Параллельно:
        - redis.get_context(user_id)
        - hardfacts_repo.search_last_updated(user_id)
        - qdrant.search(user_id, query_embedding)
        """
    
    def assemble_system_prompt(self, user: Users, context: PromptContext) -> str:
        """ME.md + RULES.md + (CREATOR.md если owner) + сериализованные факты + векторные хиты."""
```

### 4.3 Модель `Messages` — сырой архив диалога (поправка 6)
- Таблица `messages`: `message_id` PK, `user_id` FK -> users,
  `direction` Literal["incoming", "outgoing"], `channel` String,
  `external_id` BigInteger nullable (id сообщения канала),
  `text` Text NOT NULL, `created_at` DateTime(tz)
- Индекс `(user_id, created_at)`
- Worker пишет запись при получении и при отправке
- **Consolidation НЕ удаляет архив** — только Redis-контекст
- Миграция Alembic после создания модели

---

## **Этап 5: LLM-слой и skills**

### 5.1 `app/brain/llm_choice/base.py`
```python
class LLMProvider(ABC):
    @abstractmethod
    async def generate(
        self,
        prompt: str,
        tools: list[ToolSpec] | None = None,
    ) -> LLMResponse: ...

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

### 5.2 `app/brain/llm_choice/gemini.py`
- `GeminiProvider(LLMProvider)`:
  - Инициализация: `google.genai.Client(api_key=...)`
  - `generate()`:
    - Для dialogue: `client.models.generate_content(model="gemini-2.0-pro", ...)`
    - Для compact_session: `model="gemini-2.0-flash"`
    - Парсинг response -> LLMResponse
    - Обработка tool_calls (function calling)
  - `embed()`: `client.models.embed_content(model="text-embedding-004", ...)`

### 5.3 `app/brain/llm_choice/openrouter.py`
- `OpenRouterProvider(LLMProvider)`:
  - HTTP-клиент через httpx к `https://openrouter.ai/api/v1/`
  - Тот же интерфейс `generate()` / `embed()`
  - API-ключ в `Authorization: Bearer`

### 5.4 `app/brain/llm_choice/router.py`
```python
class LLMRouter:
    def __init__(self, gemini: GeminiProvider, openrouter: OpenRouterProvider):
        self._primary = gemini
        self._fallback = openrouter

    def get_provider(
        self,
        role: Literal[
            "dialogue", "compact_session", "background_parser", "vector_search"
        ],
    ) -> LLMProvider:
        """Выбор провайдера в зависимости от роли и настроек."""

    async def generate_with_fallback(
        self,
        role: Literal[
            "dialogue", "compact_session", "background_parser", "vector_search"
        ],
        prompt: str,
        tools: list[ToolSpec] | None = None,
    ) -> LLMResponse:
        """Сначала primary, при ошибке -> fallback."""
```

### 5.5 `app/brain/skills/registry.py`
```python
@dataclass
class SkillDefinition:
    name: str
    description: str
    tier: Literal["safe", "elevated", "sandboxed", "system"]
    input_schema: type[BaseModel]
    handler: Callable[..., Awaitable[SkillResult]]


class SkillRegistry:
    def __init__(self, audit_logs_repo: AuditLogsRepo):
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> None:
        """Зарегистрировать skill."""

    def list_available(
        self, user: Users, deployment: DeploymentConfig
    ) -> list[SkillDefinition]:
        """Фильтр по user.role + deployment config."""

    async def dispatch(
        self, user: Users, deployment: DeploymentConfig, name: str, args: dict
    ) -> SkillResult:
        """
        1. Проверка существования skill
        2. Проверка прав (дублирующая — не доверять list_available)
        3. Валидация args через input_schema
        4. Вызов handler
        5. AuditLogsRepo.create(...)
        6. Возврат результата
        """
```

### 5.6 `app/brain/skills/sandbox.py`
```python
class SandboxExecutor:
    def __init__(self):
        self._docker = docker.from_env()  # или Docker SDK
    
    async def run(self, code: str, timeout: float = 10.0) -> SandboxResult:
        """
        1. Создать контейнер (image='python:3.13-slim' или 'alpine')
        2. --network none, read-only rootfs, CPU/RAM limits
        3. Запустить код, таймаут
        4. Собрать stdout/stderr/exit_code
        5. finally: убить и удалить контейнер
        """
```

### 5.7 Инициализация коллекции Qdrant
- При bootstrap Worker'а: создать коллекцию `nodya_memory`,
  если отсутствует
- Параметры: размер вектора по embedding-модели (text-embedding-004
  = 768), distance = Cosine
- Payload-индекс: `user_id` (keyword) — фильтрация фактов по юзеру

---

## **Этап 6: Channel Senders — доставка ответов пользователю**

Отдельные consumer'ы (по одному на канал). Подписываются на очередь `outgoing_messages`, фильтруют по `channel`, отправляют через соответствующий API.

### 6.1 `app/senders/base.py`
```python
class ChannelSender(ABC):
    """Базовый класс для всех отправителей."""
    
    @abstractmethod
    async def send(self, user_id: UUID, text: str) -> bool:
        """Отправить сообщение. Вернуть True при успехе."""
    
    async def run(self):
        """Бесконечный цикл: consume -> filter -> send -> ACK."""
    
    async def handle_outgoing(self, message: OutgoingMessage) -> None:
        """
        1. Проверить message.channel == self._channel
        2. Проверить delay_until (если в будущем — перепубликовать с TTL)
        3. send()
        4. ACK
        """
```

### 6.2 `app/senders/tg_sender.py` — Telegram Sender
- Consumer на `outgoing_messages`, `if message.channel == "telegram"`
- `aiogram.Bot.send_message(chat_id=..., text=...)`
- **Поиск chat_id:** по `message.user_id` ищем `telegram_id` в Users (через UsersRepo)
- Retry при network error (экспоненциальный backoff, до 3 попыток)

### 6.3 `app/senders/browser_sender.py` — Browser Sender
- Consumer на `outgoing_messages`, `if message.channel == "browser"`
- `WebSocketManager.send_to_user(message.user_id, message.text)`
- Если сокет закрыт — логировать как недоставленное, не падать

### 6.4 Отложенные сообщения (delayed) — РЕШЕНО (поправка 2)
- `OutgoingMessage` содержит поле `delay_until: datetime | None`
- Механизм — **Redis ZSet + APScheduler**:
  - Сообщение с будущим временем не публикуется в
    `outgoing_messages`; Worker кладёт его в ZSet `nodya:scheduled`
    (score = unix ts отправки, member = JSON сообщения)
  - APScheduler-job каждые `SCHEDULED_POLL_SECONDS` (~30с) переносит
    созревшие member'ы в `outgoing_messages`
- RabbitMQ per-message TTL **отклонён**: очередь проверяет TTL только
  у головы — одно долгое сообщение блокирует все последующие

### 6.5 Проактивная отправка в любой канал
- Worker публикует `OutgoingMessage` с `channel="telegram"` или `channel="browser"` и т.д.
- Channel Sender соответствующего канала забирает и отправляет
- Единый механизм для всех каналов — никакой логики отправки внутри Worker'a

---

## **Этап 7: Фоновые задачи (проактивное поведение)**

### 7.0 Модель `FeedSources` — источники парсера (поправка 7)
- Таблица `feed_sources`: `source_id` PK, `url` String unique,
  `kind` Literal["rss", "telegram"], `title` String nullable,
  `is_active` Boolean default True,
  `last_checked_at` DateTime(tz) nullable, `created_at`
- Управление источниками позже — через skills tier `system`
- Миграция Alembic после создания модели

### 7.1 `app/brain/skills/background.py` — `BackgroundParserJob`
```python
class BackgroundParserJob:
    """APScheduler job. Парсинг RSS/TG-каналов."""
    
    async def run(self):
        """Обход источников -> filter_relevance -> publish/upsert."""
    
    async def filter_relevance(self, item: FeedItem) -> RelevanceScore:
        """Через LLMRouter (background_parser роль)."""
```

### 7.2 `app/brain/skills/consolidation.py` — `ConsolidationJob`
```python
class ConsolidationJob:
    """Фаза сна. Извлечение фактов из контекста диалога."""
    
    async def run(self, user_id: UUID):
        """get_context -> extract_facts -> upsert HardFacts -> embed -> upsert Qdrant -> clear_context."""
    
    async def extract_facts(self, messages: list[ContextMessage]) -> list[HardFacts]:
        """Через LLMRouter (compact_session роль). Парсинг JSON из ответа."""
```

### 7.3 `HardFactsRepo.apply_confidence_decay`
```python
async def apply_confidence_decay(self) -> None:
    """
    UPDATE hard_facts SET confidence = confidence * 0.95 WHERE updated_at < now() - interval '7 days';
    DELETE FROM hard_facts WHERE confidence < 0.1;
    """
```

---

## **Этап 8: Надёжность**

### 8.1 Graceful shutdown

Так как API Gateway, Worker и Channel Senders — раздельные процессы, shutdown обрабатывается отдельно для каждого.

- **API Gateway:**
  - FastAPI lifespan handler: при сигнале `SIGTERM` перестаёт принимать новые запросы, закрывает WebSocket-соединения, закрывает пул соединений
  - Worker и Senders не зависят от него — продолжают работать

- **Worker:**
  - `stop()`: отписаться от очереди `incoming_messages` (новые сообщения остаются в RabbitMQ)
  - Таймаут ~30с на завершение текущего handle_message
  - Закрытие пулов (PG, Redis, Qdrant)

- **Channel Senders:**
  - `stop()`: отписаться от `outgoing_messages`
  - Завершить текущую отправку
  - Закрыть соединения (Telegram Bot API, WebSocket)
      asyncio.create_task(worker.run())
      yield
      # Shutdown
      await worker.stop()
      await publisher.close()
  ```
- Заменить стандартный `app.on_event("startup")` / `("shutdown")`

### 8.2 `retry_with_backoff`
```python
async def retry_with_backoff(
    func: Callable[..., Awaitable],
    max_attempts: int = 3,
    base_delay: float = 1.0,
) -> Any:
    """Экспоненциальный backoff для LLM-вызовов.
    Retry только на network errors/timeout, не на 4xx."""
```

### 8.3 DLQ (Dead Letter Queue)
- Настроить в RabbitMQ: `incoming_messages` с `x-dead-letter-exchange` -> `nodya_dlq`
- Worker NACK'ает сообщение без `requeue=True` после 3 неудачных попыток

---

## **Этап 9: Тесты и документация**

### 9.1 `tests/conftest.py`
- Фикстуры: `async_session`, `redis_client`, `worker`, `client` (FastAPI TestClient)
- `pytest-asyncio` с `event_loop` фикстурой

### 9.2 `tests/test_models/`
- `test_users_model.py` — создание User, проверка constraints
- `test_auth_tokens.py` — FK на users, unique
- `test_hard_facts.py` — upsert логика
- `test_audit_logs.py` — запись аудита

### 9.3 `tests/test_repositories/`
- Mock `AsyncSession` через `aiosqlite` или `pytest-mock`
- `test_base_repo.py` — generic CRUD
- `test_hard_facts_repo.py`

### 9.4 `tests/test_api/`
- `test_auth.py` — register/login/401 scenarios
- `test_health.py`
- `test_webhook.py` — Telegram webhook (mock `publish_incoming`)
- `test_deps.py` — get_current_user (valid/expired/revoked token)

### 9.5 `tests/test_brain/`
- `test_llm_router.py` — fallback logic
- `test_skill_registry.py` — permission checks
- `test_redis_client.py` — с поднятым Redis (testcontainers или fakeredis)
- `test_worker.py` — полный flow с моками

### 9.6 `tests/test_worker.py`
- `test_handle_message` — полный цикл обработки сообщения
- `test_resolve_user` — создание при первом сообщении
- `test_assembly_system_prompt`

### 9.7 Документация
- **README.md** (корневой) — описание, установка, примеры
- **CHANGELOG.md** — вести по мере разработки
- **docs/** — архитектура, roadmap уже есть

---

## **Этап 10: Остальные каналы (после MVP)**

### 10.1 Discord
- `app/api/chats/ds/` — webhook от Discord (interactions endpoint)
- Pydantic-модель `DiscordInteraction`
- Outgoing: `discord.py` или `httpx` к Discord API

### 10.2 CLI
- Отдельный Python-скрипт или пакет
- `pip install nodya-cli` -> Команда `nodya ask "..."` -> HTTP-запрос к `/api/chats/browser/send`

---

## **Инкремент R3: Рефакторинг LLM-провайдеров (2026-08-30)**

### R3.1 - [x] `ProviderRegistry` — центральный реестр с ленивой инициализацией
- Файл: `app/brain/llm_choice/registry.py` (NEW)
- Регистрация фабрик провайдеров по имени
- `get(name)` — lazy init + проверка `enabled`
- `close_all()` — graceful shutdown

### R3.2 - [x] `GeminiCloudflareProvider` — полная реализация на httpx
- Файл: `app/brain/llm_choice/gemini_from_cloudflare.py` (REWRITE)
- `POST /chat/completions` (OpenAI format) для chat
- `POST /embeddings` (OpenAI format) для embeddings
- Auth: `Authorization: Bearer {GEMINI_API_KEY}`
- Ошибки: 429/5xx → LLMTransientError, 4xx → LLMFatalError
- **Без стриминга**

### R3.3 - [x] Конфиг в `config.py`
- `GEMINI_CLOUDFLARE_URL: str = "https://geminifix.shayden.workers.dev/"`
- `GEMINI_ENABLED: bool = False` — старый провайдер выключен
- `LLM_PROVIDER_CHAINS: dict` — цепочки по ролям (dialogue/cs/bp/vs)

### R3.4 - [x] `LLMRouter` рефактор под реестр
- Конструктор: `__init__(self, registry: ProviderRegistry)`
- `_chain(role)` читает `settings.LLM_PROVIDER_CHAINS[role]`
- Провайдеры получает через `registry.get(provider_name)`

### R3.5 - [x] `main.py` — сборка через реестр
- Создание `ProviderRegistry(settings)`
- Регистрация: `gemini_cloudflare`, `openrouter` (и `gemini` если enabled)
- Передача реестра в `LLMRouter`

### R3.6 - [x] Тесты
- `tests/unit/llm/test_gemini_cloudflare.py` (NEW)
- Адаптация `test_router.py` под мокирование `registry.get()`

### R3.7 - [x] Документация
- `docs/ARCHITECTURE_FULL.md` — §5.4, добавление схемы реестра
- `docs/README_PROJECT.md` — технологии + env
- `docs/TODO_MASTER.md` — этот инкремент

---

## - [?] (ПЕРЕСМОТРЕТЬ blacklist!!! Очень легко обходиться, искать другое решение.) **Этап 11: Nodya Agent**

### 11.1 `shared/schemas.py`
```python
class SkillRequest(BaseModel):
    request_id: UUID
    skill_name: str
    args: dict


class SkillResult(BaseModel):
    request_id: UUID
    success: bool
    payload: Any
    error: str | None
```

### 11.2 `agent/main.py`
- Подключение к RabbitMQ
- Consumer на очереди `agent_commands:{owner_user_id}`
- Раз в 5с heartbeat в Redis (`set_agent_online`)
- Обработка SIGTERM

### 11.3 `agent/consumer.py`
```python
class AgentConsumer:
    async def run(self):
        """Подключение к RabbitMQ, declare queue, consume."""
    
    async def execute_skill(self, request: SkillRequest) -> SkillResult:
        """Выбор handler по request.skill_name, выполнение, обёртка ошибок."""
    
    async def send_heartbeat(self):
        """RedisClient.set_agent_online(owner_user_id)."""
```

### 11.4 `agent/skills/shell.py`
- `execute_command(command: str) -> SkillResult` — безопасный запуск shell-команд
- Валидация: blacklist команд (rm -rf, dd, mkfs, ...)
- Таймаут: 30с

### 11.5 `agent/skills/filesystem.py`
- `read_file(path: str) -> SkillResult`
- `write_file(path: str, content: str) -> SkillResult`
- `list_directory(path: str) -> SkillResult`
- Path traversal protection: разрешены только пути внутри `AGENT_ALLOWED_PATHS`

