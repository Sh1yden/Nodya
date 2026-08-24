"""Туннель cloudflared для локальной разработки: публичный URL вебхука.

Только cloudflared quick-tunnel: tuna отклонена (RU-сегмент, обход
блокировок не тянет). Внутри Docker бинарника нет — там обязателен
явный TELEGRAM_WEBHOOK_URL.

Важно: после извлечения URL stdout процесса передаётся потоку-drain —
без него буфер пайпа (~64КБ) переполняется и туннель замолкает.
"""

import re
import subprocess
import threading
import time

from app.core import get_logger, settings

logger = get_logger(__name__)

_TIMEOUT = settings.TUNNEL_TIMEOUT
_URL_PATTERN = re.compile(r"https://[a-z0-9.-]+\.trycloudflare\.com")


def start_tunnel(port: int) -> tuple[str, subprocess.Popen]:
    """Запустить туннель, вернуть (публичный URL, процесс).

    --protocol http2: QUIC (UDP) тихо умирает за VPN/NAT — TCP-режим
    стабилен. URL из баннера появляется ДО регистрации на edge, поэтому
    после него дожидаемся строки Registered tunnel connection: без неё
    край Cloudflare отвечает пустым 404 на все пути.
    """
    cmd = [
        "cloudflared",
        "tunnel",
        "--url",
        f"http://localhost:{port}",
        "--protocol",
        "http2",
        "--output",
        "json",
    ]
    logger.info("Запуск cloudflared (http2) на порт %s...", port)
    process = _popen(cmd)
    url = _read_url(process)
    if url is None:
        process.terminate()
        raise RuntimeError(
            f"cloudflared не отдал URL за {_TIMEOUT}с "
            "(или завершился с ошибкой — см. логи выше)."
        )
    if not _wait_registered(process):
        process.terminate()
        raise RuntimeError(
            "cloudflared не зарегистрировал соединение на edge "
            f"за {_TIMEOUT}с — туннель не рабочий."
        )
    logger.info("Туннель готов: %s", url)
    time.sleep(2)
    _drain_stdout(process)
    return url, process


def stop_tunnel(process: subprocess.Popen | None) -> None:
    """Остановить процесс туннеля при graceful shutdown."""
    if process is None:
        return
    try:
        process.terminate()
        process.wait(timeout=3)
        logger.info("Туннель остановлен.")
    except Exception:
        try:
            process.kill()
            logger.warning("Туннель снят принудительно.")
        except Exception as exc:
            logger.error("Не удалось завершить туннель: %s", exc)


def _popen(cmd: list[str]) -> subprocess.Popen:
    """Popen с понятной ошибкой, если бинарника нет в системе."""
    try:
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Бинарник не найден: {cmd[0]}. Установите cloudflared "
            "или задайте TELEGRAM_WEBHOOK_URL."
        ) from exc


def _read_url(process: subprocess.Popen) -> str | None:
    """Блокирующе читать логи до появления публичного URL."""
    started_at = time.monotonic()

    assert process.stdout is not None
    for line in process.stdout:
        if time.monotonic() - started_at > _TIMEOUT:
            return None
        line = line.strip()
        if not line:
            continue
        match = _URL_PATTERN.search(line)
        if match:
            return match.group(0)
        logger.debug("[cloudflared] %s", line[:200])
    return None


def _wait_registered(process: subprocess.Popen, timeout: int = 20) -> bool:
    """Дождаться фактической регистрации туннеля на edge."""
    started_at = time.monotonic()

    assert process.stdout is not None
    for line in process.stdout:
        if time.monotonic() - started_at > timeout:
            return False
        line = line.strip()
        if not line:
            continue
        logger.debug("[cloudflared] %s", line[:200])
        if "Registered tunnel connection" in line:
            return True
        if '"level":"fatal"' in line or '"level":"error"' in line:
            logger.error("[cloudflared] %s", line[:200])
    return False


def _drain_stdout(process: subprocess.Popen) -> None:
    """Фоновый drain логов туннеля на DEBUG (защита от переполнения)."""
    thread = threading.Thread(
        target=_drain_loop,
        args=(process,),
        name="cloudflared-drain",
        daemon=True,
    )
    thread.start()


def _drain_loop(process: subprocess.Popen) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        line = line.strip()
        if line:
            logger.debug("[cloudflared] %s", line[:200])
