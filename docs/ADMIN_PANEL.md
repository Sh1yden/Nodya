# Админ-панель Nodya

Read-only панель мониторинга: контейнеры, health зависимостей, очереди,
сгруппированные ошибки и хвост логов. Отдельный контейнер `nodya_admin`
из того же образа, что и `app`, но со своим процессом
`uvicorn app.admin.main:app --port 8016`.

## Доступ

- Публичный адрес: `https://admin.nodya.shayden.ru` (ingress cloudflared
  BridgeNode → `http://nodya_admin:8016`, сеть `internet_bridge`).
- Host-портов у контейнера нет; напрямую из интернета панель недоступна.
- Вход — только owner: форма логина, пароль проверяется argon2id, роль
  пользователя должна быть `owner`. Сессия — случайный opaque-токен в
  cookie `nodya_admin_session` (`HttpOnly`, `Secure`, `SameSite=Lax`,
  host-only, без `Domain`), TTL `ADMIN_SESSION_TTL_MINUTES` (по умолчанию
  720 минут).
- Авторитет сессии — Redis (`nodya:admin:session:<sha256(token)>`). Панель
  работает при лежащем PostgreSQL: на каждый запрос БД не требуется.
  Цена решения — смена роли owner применится только после истечения TTL
  или логаута (owner в проекте один, смена роли ручная).
- Redis недоступен → fail closed: страницы отвечают 503, partial'ы — 401.

### Rate limit и CSRF

- 5 неудачных попыток входа за 15 минут на хеш IP → 429; успешный вход
  сбрасывает счётчик. Ограничения per-username нет (только отображение).
- Иных state-changing запросов, кроме `/login` и `/logout`, нет.
  Для POST проверяется заголовок `Origin`: он должен совпасть с
  `ADMIN_PUBLIC_ORIGIN` либо с фактическим `Host` запроса (same-origin,
  поэтому localhost/127.0.0.1/туннель работают без перенастройки);
  opaque `Origin: null` (webview, sandbox) принимается, только если
  `Sec-Fetch-Site` не равен `cross-site`/`same-site`; иначе — 403.
  Плюс `SameSite=Lax` на cookie.

## Страницы

| Путь | Что показывает |
|---|---|
| `/` | heartbeat приложения (версия, uptime, задачи планировщика), карточки зависимостей, контейнеры, очереди, свежие ошибки, статистика PG/Redis/Qdrant |
| `/containers` | все контейнеры compose-проекта: состояние, health, образ, рестарты, uptime |
| `/containers/{service}` | карточка контейнера + хвост логов (обновление 3 c) |
| `/queues` | depth/consumers для `incoming_messages`, `outgoing_messages`, `typing_events`, `nodya_dlq` |
| `/errors` | сгруппированные ошибки из `error_events` (фильтр level) |
| `/errors/{fingerprint}` | сообщение, traceback, счётчик, first/last seen |
| `/logs` | JSONL-логи приложения: файл, level, поиск по подстроке |
| `/partials/*` | htmx-фрагменты для polling |
| `/healthz` | liveness контейнера, без авторизации |
| `/readyz` | готовность зависимостей, только owner (200/503) |

Неаутентифицированная страница → 303 на `/login?next=...`; partial → 401
с `HX-Redirect`.

## Read-only гарантии

- Docker API доступен только через `tecnativa/docker-socket-proxy`:
  `POST=0`, разрешены `CONTAINERS/NETWORKS/INFO/VERSION/PING/EVENTS`
  (`NETWORKS` нужен Alloy: discovery.docker читает метки сетей); сокет в
  контейнер admin не монтируется.
- Из inspect читается allowlist полей (`State.Status/Health/StartedAt`,
  `RestartCount`, `Config.Image`, порты). `Config.Env`, `Config.Cmd`,
  `Mounts.Source` не запрашиваются и не рендерятся.
- RabbitMQ — только passive declare очередей (без создания/удаления).
- PostgreSQL — только SELECT (`error_events`, размер БД, активные сессии).
- Никаких кнопок restart/stop: v1 полностью read-only.

## Архитектура

```
app/admin/
├── main.py        create_app(), lifespan, security headers, static/templates
├── services.py    AdminServices + TTL-кеш (health 10c, containers 5c,
│                  queues 5c, errors 10c, logs 2c, runtime 5c; stale 60c)
├── cache.py       TTLCache: per-key lock, stale-on-error, negative cache 5c
├── auth.py        login/logout, Principal, hash IP, safe next, origin
├── deps.py        require_owner, verify_origin, render_template
├── formatting.py  Jinja-фильтры (даты, байты, uptime, классы статусов)
├── collectors/    docker, broker, infra, logs, errors, runtime
├── routes/        pages, auth, partials, health
├── templates/     Jinja2 (server-rendered, тёмная тема)
└── static/        admin.css, htmx.min.js (vendored, без CDN)
```

Коллектор возвращает `Result(data|None, error, fetched_at, stale)`:
ошибка одного источника не роняет страницу — рисуется карточка с ошибкой,
данные до 60 секунд могут отдаваться как stale.

## Поток ошибок

```
logger "nodya.*" (ERROR+) -> ErrorEventSink -> asyncio.Queue (1000)
  -> drainer (батч <=100 / 2 c) -> fingerprint -> upsert error_events
  -> панель /errors + nodya_error_sink_events_total
```

Fingerprint = `sha256(logger:function:exception_type:normalize(message))`;
нормализация маскирует UUID/hex/числа/токены/`key=value`-секреты.
Сообщение (2k) и traceback (8k) обрезаются и проходят redaction перед
записью и повторно перед рендером в панели. Хранение —
`ERROR_EVENTS_TTL_DAYS` (30 дней, ежедневная очистка).

## Настройки `.env`

```dotenv
DOCKER_PROXY_URL=http://docker-proxy:2375
ADMIN_SESSION_TTL_MINUTES=720
ADMIN_COOKIE_SECURE=True
ADMIN_COMPOSE_PROJECT=nodya
ADMIN_LOGS_DIR=logs
ADMIN_PUBLIC_ORIGIN=https://admin.nodya.shayden.ru
ADMIN_ALLOWED_HOSTS=admin.nodya.shayden.ru,localhost,127.0.0.1,testserver,nodya_admin
GRAFANA_PUBLIC_URL=https://grafana.nodya.shayden.ru
ERROR_EVENTS_ENABLED=True
ERROR_EVENTS_TTL_DAYS=30
```

`SettingsSchema` использует `extra="forbid"`: compose-only переменные
(`GF_*`, `NODYA_IMAGE_TAG`) в `.env` не добавлять — они кладутся в
`observability/grafana.env` или в `environment:` compose.

## Локальный запуск

```bash
ADMIN_COOKIE_SECURE=false
ADMIN_PUBLIC_ORIGIN=http://localhost:8016
ADMIN_ALLOWED_HOSTS=localhost,127.0.0.1
```

Затем `uv run uvicorn app.admin.main:app --port 8016`. Без этих
переопределений браузер молча выбросит `Secure`-cookie и получится
«петля логина». Docker API при локальном запуске недоступен —
карточка контейнеров покажет ошибку, остальное работает.

## Безопасность

- `TrustedHostMiddleware` по `ADMIN_ALLOWED_HOSTS`.
- CSP `default-src 'self'; frame-ancestors 'none'`, `X-Frame-Options:
  DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  no-referrer`, `Cache-Control: no-store` для HTML.
- Jinja autoescape; `|safe` для логов/ошибок не используется.
- `/docs`, `/redoc`, `/openapi.json` отключены.
- Контейнер: `cap_drop: ALL`, `no-new-privileges`, без портов.

## Диагностика

| Симптом | Причина / что делать |
|---|---|
| Редирект на `/login` по кругу | `ADMIN_COOKIE_SECURE=true` на http-локальном стенде — выключить флаг |
| 503 на всех страницах | Redis недоступен (fail closed) — поднять `redis` |
| 400 на любом хосте | хост не в `ADMIN_ALLOWED_HOSTS` |
| 403 при логине | `Origin` не совпал ни с `ADMIN_PUBLIC_ORIGIN`, ни с `Host`; в логах admin — `Admin origin rejected: origin=... host=...` |
| 429 | 5 неудач за 15 минут на IP — подождать окно |
| Карточки серые/с ошибкой | соответствующий источник недоступен; данные могут быть stale до 60 c |
| Пустая страница ошибок | `ERROR_EVENTS_ENABLED=false` или ошибок ещё не было |
