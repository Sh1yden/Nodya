# Changelog

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
