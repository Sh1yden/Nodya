# Changelog

## 2026-10-29 — медиа: подробные описания

### Vision
- Промпт распознавания переписан: 2–4 плотных предложения (цель 200–450 знаков), фасеты по значимости (субъект/действие, люди, фон, объекты), дословный OCR, запрет домыслов; подпись пользователя передаётся как контекст
- Маркер вложения стал самодокументируемым: `[Вложение: фото | Распознано: … | Детали: …]`; для гиф/видео явно указан кадр-превью, неудачи помечаются `Статус: превью недоступно / не удалось распознать`
- `MEDIA_DESCRIPTION_MAX_CHARS` 400 → 600; при превышении текст режется по последнему целому предложению (fallback — по границе слова), многоточие помечает потерянный хвост
- В RULES добавлено правило чтения маркеров `[Вложение: …]`

## 2026-10-29 — D8 (источники новостей и авто-поиск)

### Sources (7.4)
- Пакет `app/brain/feed/`: резолвер URL (публичные Telegram `t.me/s/<channel>`, YouTube `/channel` и `/@handle` → RSS, автопоиск `<link rel="alternate">` на сайтах), фетчеры RSS/Atom и Telegram, `FeedSourceService`, генерация реакции Ноди
- Пакет `app/brain/search/`: цепочка Tavily → Brave → DuckDuckGo с фолбэком; провайдеры без ключа пропускаются
- Недельный `FeedDiscoveryJob` по фактам владельца: до 3 запросов, ≤12 кандидатов (1 на хост), порог 8/10, ≤2 источников, одно уведомление
- `BackgroundParserJob`: бюджет `FEED_MAX_ITEMS_PER_RUN`, уведомления не чаще `FEED_NOTIFY_DAILY_LIMIT` в сутки (Redis-квота), реакция после новости

### Management
- Owner-команды `/feed add|list|rm` (только личный чат) и CLI `python -m app.brain.feed_cli add|list|rm|discover`
- Автоподхват голых ссылок на каналы/фиды от owner с квотой `FEED_AUTOCAPTURE_DAILY_LIMIT`
- Потолок `FEED_MAX_ACTIVE_SOURCES`, мягкое удаление и память о выключенных источниках

### Tests
- 391 тест: резолвер, фетчеры, сервис, реакция, discovery, поисковая цепочка, Redis-квоты, команды Worker и автоподхват, бюджет скана

## 2026-10-28 — D7 (проактивность, RSS, decay)

### Proactive dialogue (4.2)
- `_proactive_decision` в Worker: 70% ответить сразу, 20% отложить доставку на 5 мин..2 ч, 10% пропустить (батч запоминается без ответа)
- Сквозная отложенная доставка: `OutgoingMessage.delay_until` → `ScheduledEnvelope(kind="outgoing")` в `nodya:scheduled`

### Feed parser (7.0–7.1)
- `FeedSources` модель + миграция `c5868b27f719` (`ck_feed_sources_kind_valid`), `FeedSourcesRepo`
- `BackgroundParserJob`: RSS → `bp`-скоринг релевантности 0..10, Redis-дедупе (`claim_feed_item`), уведомление owner при score ≥ `FEED_NOTIFY_SCORE`, запись в Qdrant при score ≥ `FEED_STORE_SCORE`
- Зависимость `feedparser==6.0.14`

### Memory (7.3)
- `HardFactsRepo.apply_confidence_decay` + ежедневный job: факты старше `CONFIDENCE_DECAY_AFTER_DAYS` теряют `CONFIDENCE_DECAY_FACTOR` уверенности, ниже `CONFIDENCE_DECAY_MIN_CONFIDENCE` удаляются

### Tests
- 284 теста (unit + integration), проактивность отключена в worker-тестах через autouse fixture

## 2026-10-28 — D6 (медиа-пайплайн)

### Media
- Нормализация TG-медиа: gif/animation, фото, стикеры, видео, видеозаметки; `source_kind` в архиве сообщений + миграция
- Vision-модуль с цепочкой провайдеров, описания медиа в промпте и Redis-истории, фильтрация модальностей в роутере
- `MEDIA_IMAGE_MAX_BYTES`, `MEDIA_DESCRIPTION_MAX_CHARS`, `MEDIA_MAX_PER_BATCH`

## 2026-09-01 — R4 (харднинг и typing)

### Security
- `verify_password` ловит `Argon2Error` (фикс 500 для tg-юзеров), dummy-hash против тайминга, `has_usable_password`
- `POST /auth/logout` (`revoked_at`), `one_owner` partial index, `CHECK role`, `bootstrap` CLI (ADR-16)
- `GEMINI_CLOUDFLARE_URL` без дефолта на личный воркер, `extra=forbid`

### LLM
- `ProviderRegistry.close_all` async, `_chain` lazy resolve (деградация вместо краша)
- 3-уровневые цепочки `dialogue/cs/bp` (`haiku`, `llama`, `qwen`) + роль `media`

### Infra
- `Dockerfile` `USER app` + `HEALTHCHECK`, `docker-compose` порты `127.0.0.1`, `app` healthcheck
- `Base` `naming_convention` + фикс `abbdbc96f127 downgrade`
- `TGSender` ловит `TelegramAPIError` без зависания, `consolidation` CLI через реестр

### Typing (ADR-17)
- `TypingEvent` + `typing_events` очередь, `app/chats/typing.py` канал-агностик, Worker публикует `start/stop`, Sender loop 4с

## 2026-08-30 — R3
- `ProviderRegistry`, `GeminiCloudflareProvider`, `LLM_PROVIDER_CHAINS`, `GEMINI_ENABLED=false`

## 2026-08-27 — D5
- Тесты (176 unit+integration)

## 2026-08-26 — L1
- Линковка Telegram `POST /auth/telegram/code` + `/link` мёрж

## 2026-08-25 — D4 / R1 / R2
- Векторная память Qdrant, consolidation, рефактор структуры, англофикация

## 2026-08-24 — E / B / C / D1 / H / N / D2 / D3
- Инициализация, инфра, вертикальный срез TG, auth, tuning
