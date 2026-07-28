# Nodya — детальный roadmap с контрактами

Продолжение `Nodya_architecture_audit_roadmap.md`. Здесь то же самое дерево этапов, но каждый пункт, за которым стоит функция, метод или класс, снабжён контрактом — что он принимает и что возвращает. Это не код, а спецификация интерфейса: реализацию пишешь сам, тип возврата и обязанности зафиксированы, чтобы не изобретать сигнатуру на ходу посреди написания worker'а.

Нотация: `функция(параметр: Тип, ...) -> ТипВозврата` — описание. Для классов: назначение класса, затем методы в той же нотации.

---

## Общие типы (используются в контрактах ниже)

Небольшой общий словарь, чтобы не повторять определения в каждом этапе:

- `User` — строка `Users` (после фикса из этапа 0): `user_id`, `telegram_id`, `discord_id`, `username`, `password_hash`, `role: Literal["owner", "user"]`, `settings`.
- `UserState` — `status: Literal["idle", "thinking", "sleeping"]`, `last_active_at: datetime`.
- `ContextMessage` — `role: Literal["user", "assistant"]`, `content: str`, `timestamp: datetime`.
- `PromptContext` — `history: list[ContextMessage]`, `facts: list[HardFacts]`, `vector_hits: list[VectorHit]`.
- `VectorHit` — `text: str`, `score: float`, `source: str`.
- `IncomingMessage` — `user_external_id: str`, `channel: Literal["telegram", "discord", "browser", "cli"]`, `text: str`, `received_at: datetime`.
- `OutgoingMessage` — `user_id: UUID`, `channel: str`, `text: str`.
- `LLMResponse` — `text: str | None`, `tool_calls: list[ToolCall]`.
- `ToolCall` — `name: str`, `arguments: dict`.
- `ToolSpec` — `name: str`, `description: str`, `parameters_schema: dict` (JSON Schema для LLM function calling).
- `SkillDefinition` — `name: str`, `description: str`, `tier: Literal["safe", "elevated", "sandboxed", "system"]`, `input_schema: type[BaseModel]`, `handler: Callable`.
- `SkillResult` — `success: bool`, `payload: Any`, `error: str | None`.
- `SkillRequest` (в `shared/`, для RPC с Agent) — `request_id: UUID`, `skill_name: str`, `args: dict`.
- `DeploymentConfig` — `system_skills_enabled: bool`, `sandbox_enabled: bool`.
- `HealthStatus` — `postgres: bool`, `redis: bool`, `rabbitmq: bool`, `qdrant: bool`.
- `RelevanceScore` — `score: float`, `reason: str`.
- `FeedItem` — `source: str`, `title: str`, `content: str`, `url: str`, `published_at: datetime`.
- `ProactiveDecision` — `action: Literal["respond_now", "schedule", "skip"]`, `delay_seconds: int | None`.
- `SandboxResult` — `stdout: str`, `stderr: str`, `exit_code: int`, `timed_out: bool`.

---

## Этап 0 — Фикс того, что уже сломано

- [x] `.env.example`
- [x] `all` → `__all__`
- [x] `root_prefix` логгера
- [x] Убрать `browser_id`/`cli_id` из `Users`
- [x] Модель `AuthTokens`
- [ ] Пересоздать init-миграцию

Контрактов на этом этапе немного — в основном изменения полей моделей (описаны в основном документе, п. 3.1), не отдельные функции. Единственное, что стоит зафиксировать сразу:

- [x] `hash_password(password: str) -> str` — хэширование через argon2id, используется и в register, и при первом апдейте пароля.
- [x] `verify_password(password: str, password_hash: str) -> bool` — сверка при логине.

Обе — чистые функции без сайд-эффектов, разместить рядом с `UsersRepo` (например `app/brain/repositories/security.py`), чтобы `AuthTokens`/`Users` repo их переиспользовали.

---

## Этап 1 — Инфраструктурный фундамент

?
Только конфигурация (Dockerfile, docker-compose, pyproject) — функций/классов нет, контракты не нужны.

---

## Этап 2 — Redis-слой памяти (short-term)

### `class RedisClient`
Обёртка над `redis.asyncio.Redis` с пулом соединений. Единая точка входа для state/context/lock/debounce — остальной код никогда не работает с `redis-py` напрямую.

- `get_state(user_id: UUID) -> UserState | None` — читает Hash `nodya:state:{user_id}`, `None` если ключа нет.
- `set_state(user_id: UUID, status: str, ttl: int | None = None) -> None` — пишет статус, опционально с TTL.
- `push_context(user_id: UUID, message: ContextMessage) -> None` — добавляет сообщение в Capped List `nodya:context:{user_id}`, обрезает список до N последних, обновляет TTL (24ч).
- `get_context(user_id: UUID, limit: int = 20) -> list[ContextMessage]` — последние `limit` сообщений, в хронологическом порядке.
- `clear_context(user_id: UUID) -> None` — используется Consolidation-джобой после переноса в Postgres/Qdrant.
- `acquire_lock(user_id: UUID, ttl: int = 30) -> bool` — `SET NX EX`, возвращает `True` если лок захвачен, `False` если уже занят.
- `release_lock(user_id: UUID) -> None` — снимает лок (проверить, что снимает именно тот, кто ставил — через уникальный token в значении ключа, иначе гонка при истечении TTL).
- `is_locked(user_id: UUID) -> bool`.
- `push_debounce(user_id: UUID, text: str) -> int` — добавляет сообщение в буфер, возвращает текущий размер буфера (нужно, чтобы решить, перезапускать ли таймер задержки).
- `pop_debounce_batch(user_id: UUID) -> list[str]` — атомарно забирает и очищает буфер (по достижении задержки в 5 сек).
- `set_agent_online(user_id: UUID, ttl: int = 15) -> None` — presence-ключ для Nodya Agent (этап 11).
- `is_agent_online(user_id: UUID) -> bool`.

---

## Этап 3 — API Gateway (FastAPI)

### Эндпоинты (`app/api/chats/tg/`, `app/api/auth/`)

- `telegram_webhook(update: TelegramUpdate) -> Response` — валидирует пришедший от Telegram апдейт (стандартная pydantic-модель под Bot API), извлекает `tg_id`+текст, паблишит `IncomingMessage` в `incoming_messages`, отвечает `202 Accepted` сразу, не дожидаясь обработки.
- `register(payload: RegisterRequest) -> RegisterResponse` — `RegisterRequest {username, email, password}`. Хэширует пароль, создаёт `Users`. Первый зарегистрированный пользователь в пустой БД — либо получает `role="owner"` автоматически, либо роль сверяется с `OWNER_EMAIL` из конфига (реши, какой вариант надёжнее для себя — второй устойчивее к пересозданию БД). `RegisterResponse {user_id, token}`.
- `login(payload: LoginRequest) -> LoginResponse` — `LoginRequest {username, password}`, сверяет пароль, генерирует opaque-токен, кладёт хэш в `AuthTokens`, возвращает `LoginResponse {token}` (plaintext-токен только в этом ответе, дальше только хэш в БД).
- `health_check() -> HealthStatus` — пингует Postgres/Redis/RabbitMQ/Qdrant параллельно (`asyncio.gather`), не роняет процесс при частичном отказе — используется в фазе Bootstrap для fail-fast и как `/health` для мониторинга.

### Dependency

- `get_current_user(token: str) -> User` — FastAPI-dependency: хэширует токен из `Authorization: Bearer`, ищет в `AuthTokens`, возвращает `User` или кидает `401`. Используется всеми защищёнными эндпоинтами кроме `/webhook` (тот аутентифицируется секретом Telegram, не токеном пользователя) и `/health`.

### `class MessagePublisher`
Обёртка над `aio-pika` каналом для публикации.

- `publish_incoming(message: IncomingMessage) -> None`.
- `publish_outgoing(message: OutgoingMessage) -> None`.

---

## Этап 4 — Worker: основной цикл

### `class Worker`
Главный orchestrator. Один экземпляр на процесс `worker.py`, живёт всё время работы приложения (Active Loop из фазы 2).

- `run() -> None` — запускает consumer на `incoming_messages`, для каждого сообщения вызывает `handle_message`, бесконечный цикл до `SIGTERM`.
- `handle_message(message: IncomingMessage) -> None` — весь путь одного сообщения: резолв пользователя → лок → сборка контекста → генерация ответа → паблиш в `outgoing_messages` → анлок → ACK. Ошибки внутри — не роняют весь Worker, логируются и NACK'аются (сообщение уходит в retry/DLQ).
- `resolve_user(channel: str, external_id: str) -> User` — ищет пользователя по `telegram_id`/`discord_id`; если не найден — создаёт нового (для TG/DS, где идентификатор внешний и создание нового юзера на лету оправдано; для browser/cli такого не бывает — там только через `register`/`login`).
- `build_context(user_id: UUID) -> PromptContext` — параллельно тянет: `RedisClient.get_context`, `HardFactsRepo.search_last_updated`, поиск по Qdrant.
- `assemble_system_prompt(user: User, context: PromptContext) -> str` — `ME.md` + `RULES.md` + (`CREATOR.md`, если `user.role == "owner"`) + сериализованные `context.facts`/`context.vector_hits`.

---

## Этап 5 — LLM-слой и skills

### `abstract class LLMProvider`
Общий интерфейс под все 4 роли (Dialogue/Compact-Session/Background-Parser/Vector-Search).

- `generate(prompt: str, tools: list[ToolSpec] | None = None) -> LLMResponse` — абстрактный метод.

### `class GeminiProvider(LLMProvider)`, `class OpenRouterProvider(LLMProvider)`
Конкретные реализации того же интерфейса, отличаются только клиентом внутри `generate`.

### `class LLMRouter`
- `get_provider(role: Literal["dialogue", "compact_session", "background_parser", "vector_search"]) -> LLMProvider`.
- `generate_with_fallback(role: ..., prompt: str, tools: list[ToolSpec] | None = None) -> LLMResponse` — вызывает основного провайдера, при ошибке/таймауте — fallback-провайдера того же уровня (если решено делать fallback активным, см. открытый вопрос в основном документе).

### `class SkillRegistry`
Единая точка регистрации и диспетчеризации всех skills, с проверкой прав перед вызовом.

- `register(skill: SkillDefinition) -> None` — регистрирует skill при старте приложения.
- `list_available(user: User, deployment: DeploymentConfig) -> list[SkillDefinition]` — фильтрует реестр: `safe`/`elevated` — всегда; `sandboxed` — если `deployment.sandbox_enabled`; `system` — только если `user.role == "owner"` и `deployment.system_skills_enabled`. Список из этого метода уходит в LLM как `tools`.
- `dispatch(user: User, deployment: DeploymentConfig, skill_name: str, args: dict) -> SkillResult` — повторяет ту же проверку прав (не доверять тому, что LLM выбрала только из `list_available` — она может «придумать» вызов), валидирует `args` по `input_schema`, вызывает `handler` (или делегирует в `SandboxExecutor`/Agent-RPC для tier `sandboxed`/`system`), пишет запись в `AuditLogsRepo`.

### `class SandboxExecutor`
- `run(code: str, timeout: float = 10.0) -> SandboxResult` — поднимает эфемерный контейнер (`--network none`, read-only rootfs), выполняет, гарантированно уничтожает контейнер в `finally` независимо от результата.

---

## Этап 6 — Ответ пользователю

- `handle_outgoing_message(message: OutgoingMessage) -> None` — consumer на `outgoing_messages` в `app/api/chats/tg/`: достаёт сообщение, шлёт через Telegram Bot API, ACK по успеху.

---

## Этап 7 — Проактивное поведение и фон

- `decide_proactive_action(user: User, state: UserState) -> ProactiveDecision` — реализация логики "свободна/занята" из заметок.

### `class BackgroundParserJob`
- `run() -> None` — cron-точка входа (APScheduler), обходит источники, вызывает `filter_relevance` для каждого, при высоком score публикует проактивное сообщение, при среднем — сохраняет в Qdrant.
- `filter_relevance(item: FeedItem) -> RelevanceScore` — вызывает `LLMRouter.get_provider("background_parser")`.

### `class ConsolidationJob` (Sleep)
- `run(user_id: UUID) -> None` — точка входа по крону/по неактивности 3ч.
- `extract_facts(messages: list[ContextMessage]) -> list[HardFacts]` — вызов Compact-Session модели, парсинг структурированного ответа.

### `HardFactsRepo` (реализовать существующий стаб)
- `search_last_updated(user_id: UUID, limit: int = 20) -> list[HardFacts]` — факты пользователя, отсортированные по `updated_at desc`.
- `apply_confidence_decay() -> None` — батч-джоба, снижает `confidence` у давно не подтверждённых фактов, удаляет упавшие ниже порога.

---

## Этап 8 — Надёжность

- `graceful_shutdown(app: FastAPI, worker: Worker) -> None` — обработчик `SIGTERM`: `app.state.accepting = False` → HTTP перестаёт принимать новые запросы → `worker.stop_consuming()` → таймаут на текущие задачи → закрытие пулов.
- `retry_with_backoff(func: Callable[..., Awaitable], max_attempts: int = 3, base_delay: float = 1.0) -> Any` — обёртка для внешних вызовов (Google AI Studio, OpenRouter), экспоненциальный backoff.

---

## Этап 9 — Тесты и документация

Без отдельных контрактов — обычные `pytest`-тесты на сигнатуры, зафиксированные выше.

---

## Этап 10 — Остальные каналы

Тот же контракт, что `app/api/chats/tg/`: `webhook`-эндпоинт своего формата → `IncomingMessage` → `incoming_messages`; `handle_outgoing_message`-аналог на приём из `outgoing_messages`. Отличается только парсинг платформенного payload, `Worker`/`SkillRegistry`/`LLMRouter` не меняются.

---

## Этап 11 — Nodya Agent

### `class AgentConsumer` (в `agent/`)
- `run() -> None` — подключается к RabbitMQ, слушает `agent_commands:{owner_user_id}`, для каждого сообщения вызывает `execute_skill`, публикует результат по `reply_to`/`correlation_id`.
- `execute_skill(request: SkillRequest) -> SkillResult` — реальная host-level реализация (shell/fs/etc.), любые исключения оборачиваются в `SkillResult(success=False, error=...)`, а не пробрасываются наружу.
- `send_heartbeat() -> None` — раз в несколько секунд обновляет presence через `RedisClient.set_agent_online` (Agent подключается к тому же Redis, что и Core).

`SkillRequest`/`SkillResult` — общие модели в `shared/`, единственная зависимость между `app/` и `agent/`.
