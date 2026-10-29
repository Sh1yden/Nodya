# Observability: Grafana + Loki + Prometheus

Стек истории метрик, поиска по логам и контейнерных метрик. Всегда включён
(без compose profile), наружу не публикуется: только Grafana доступна через
cloudflared и Cloudflare Access.

## Состав

| Сервис | Образ | Сеть | Назначение |
|---|---|---|---|
| `prometheus` | `prom/prometheus:v3.7.3` | default + monitoring | scrape 15 c, retention 30 d / 2 GB |
| `loki` | `grafana/loki:3.6.3` | monitoring | логи, tsdb v13, retention 336 h |
| `alloy` | `grafana/alloy:v1.11.3` | docker_api + monitoring | сбор docker-логов и JSONL |
| `cadvisor` | `gcr.io/cadvisor/cadvisor:v0.52.1` | monitoring | CPU/RAM/сеть/ФС контейнеров |
| `redis-exporter` | `oliver006/redis_exporter:v1.76.0` | default + monitoring | метрики Redis |
| `postgres-exporter` | `prometheuscommunity/postgres-exporter:v0.18.1` | default + monitoring | метрики PostgreSQL |
| `grafana` | `grafana/grafana:12.3.1` | monitoring + internet_bridge | дашборды, Cloudflare Access сверху |

`docker-proxy` (`tecnativa/docker-socket-proxy:v0.5.0`) — единственный
держатель `/var/run/docker.sock` (`:ro`), сеть `docker_api` (`internal`).
Alloy и admin ходят к Docker API только через него (`POST=0`, разрешены
`CONTAINERS/NETWORKS/INFO/VERSION/PING/EVENTS`; `NETWORKS` обязателен для
`discovery.docker`, который читает метки сетей).

Grafana слушает внутренний порт 3002 (`GF_SERVER_HTTP_PORT`): host-порт
3000 занят портфолио, а сам Grafana наружу не публикуется.

Сети `docker_api` и `monitoring` — `internal: true`; host-портов у
Prometheus/Loki/Alloy/cAdvisor/exporters нет. Grafana — единственный
компонент, подключённый и к `internet_bridge`.

## Первый запуск

1. Скопировать env Grafana и задать пароль:

   ```bash
   cp observability/grafana.env.example observability/grafana.env
   $EDITOR observability/grafana.env
   ```

   Файл в `.gitignore`; compose читает его как опциональный
   (`required: false`), но без пароля Grafana поднимется с `admin/admin`.

2. Поднять стек:

   ```bash
   docker compose up -d --build
   ```

3. Ingress в BridgeNode (cloudflared уже в `internet_bridge`):

   ```yaml
   - hostname: admin.nodya.shayden.ru
     service: http://nodya_admin:8016
   - hostname: grafana.nodya.shayden.ru
     service: http://nodya_grafana:3002
   ```

4. Cloudflare Access: для `grafana.nodya.shayden.ru` — policy на
   owner-email. Логин Grafana остаётся вторым слоем.
5. Проверить: Prometheus → Status → Targets (все UP), Grafana →
   Connections → Data sources (Prometheus, Loki), папка Nodya с тремя
   дашбордами.

## Prometheus

`observability/prometheus/prometheus.yml`, scrape 15 c:

| Job | Target | Метрики |
|---|---|---|
| app | `app:8015` | `nodya_webhook_updates_total`, `nodya_worker_*`, `nodya_llm_*`, `nodya_scheduler_runs_total`, `nodya_error_sink_events_total` |
| rabbitmq | `rabbitmq:15692` | `rabbitmq_queue_messages_ready`, `rabbitmq_queue_consumers` |
| redis | `redis-exporter:9121` | `redis_memory_used_bytes`, `redis_connected_clients` |
| postgres | `postgres-exporter:9187` | `pg_up`, `pg_stat_database_numbackends`, `pg_database_size_bytes` |
| cadvisor | `cadvisor:8080` | `container_cpu_usage_seconds_total`, `container_memory_working_set_bytes`, `container_network_*` |

Порт 8015 — внутренний порт процесса `app` (daemon-поток
`prometheus_client`), tunnel его не маршрутизирует. Admin-контейнер
поднимается с `METRICS_ENABLED=false`, `ERROR_EVENTS_ENABLED=false`.

Retention задан флагами `--storage.tsdb.retention.time=30d` и
`--storage.tsdb.retention.size=2GB`; admin/lifecycle API не включены.

## Loki + Alloy

Loki: single-binary, filesystem, схема `tsdb` v13, retention 336 h
(14 дней), compactor с `delete_request_store: filesystem`.
Конфиг проверяется `docker run --rm ... grafana/loki:3.6.3
-verify-config`.

Alloy (`observability/alloy/config.alloy`):

- `discovery.docker` через `tcp://docker-proxy:2375`, фильтр
  `com.docker.compose.project=nodya`;
- `loki.source.docker` — логи инфраструктуры и admin; `nodya_heart`
  исключён drop-правилом, чтобы не дублировать файловые логи;
- `local.file_match /var/log/nodya/*.jsonl` + `loki.source.file` —
  JSONL приложения; `stage.json` вытаскивает метки `level` и `module`
  (`full_module`); timestamp из JSONL не используется (там только
  `HH:MM:SS`), Loki ставит время приёма;
- метки: `job` (`app`/`docker`), `service`, `container`, `stream`,
  `level`, `module`.

## Дашборды

Провижининг: `observability/grafana/provisioning/` (datasources UID
`prometheus`/`loki`, dashboards из `/var/lib/grafana/dashboards`).

| UID | Содержимое |
|---|---|
| `nodya-overview` | up-статусы, webhook/worker/LLM, error sink, scheduler, очереди, PG-соединения, Redis |
| `nodya-containers` | CPU, memory working set, сеть, ФС, restarts за 24 ч |
| `nodya-logs` | логи infra/admin, логи приложения JSONL, объём по уровням; фильтры service/level/module |

## Runbook

| Симптом | Действия |
|---|---|
| `nodya_dlq` растёт | искать `ERROR`/`CRITICAL` в `/errors` и Loki (`{job="app"}`), разобрать батчи worker'а |
| `up{job="app"}` = 0 | контейнер упал или порт 8015 не слушается; `docker logs nodya_heart` |
| Heartbeat stale в панели | app завис/перезапускается; проверить `nodya:runtime:heartbeat` в Redis |
| Target DOWN | проверить сеть контейнера (`monitoring`/`default`), `docker logs` соответствующего сервиса |
| Ошибки в `/errors` повторяются | посмотреть count/first-seen, traceback; после фикса — запись сама перестанет расти, старые истекут по TTL |
| Диск растёт | docker json-file 10m×3, JSONL 14 d, Loki 14 d, Prometheus 30 d/2 GB; проверить `docker system df` |
| Grafana недоступна | Cloudflare Access policy, `docker logs nodya_grafana`, `GF_SERVER_ROOT_URL` |

## Retention и безопасность

- docker logs: `json-file`, `max-size: 10m`, `max-file: 3` — на всех
  сервисах (якорь `x-logging`).
- JSONL приложения: `LOG_RETENTION_DAYS=14`, задача `log-retention`.
- Loki: 14 дней; Prometheus: 30 дней / 2 GB.
- Grafana: анонимный доступ выключен, sign-up выключен, cookie Secure,
  аналитика выключена (`observability/grafana.env`).
- Prometheus/Loki/cAdvisor/exporters не имеют host-портов; `admin` — тоже.
- Секреты не попадают в логи: error sink редактирует токены/пароли,
  трейсбеки; в метках Prometheus нет user/chat id.

## Проверка конфигов

```bash
docker compose config --quiet
docker run --rm -v "$PWD/observability/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  --entrypoint promtool prom/prometheus:v3.7.3 check config /etc/prometheus/prometheus.yml
docker run --rm -v "$PWD/observability/loki/loki-config.yml:/etc/loki/loki-config.yml:ro" \
  grafana/loki:3.6.3 -config.file=/etc/loki/loki-config.yml -verify-config
docker run --rm -v "$PWD/observability/alloy/config.alloy:/etc/alloy/config.alloy:ro" \
  grafana/alloy:v1.11.3 validate /etc/alloy/config.alloy
```
