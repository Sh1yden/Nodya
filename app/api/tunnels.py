"""cloudflared quick-tunnel for local development: public webhook URL.

Tuna was rejected (RU segment). Inside Docker the binary is absent —
an explicit TELEGRAM_WEBHOOK_URL is mandatory there.

Important: after extracting the URL, the process stdout must be
drained by a background thread — otherwise the OS pipe buffer
(~64KB) fills up and the tunnel silently freezes.
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
    """Start a tunnel and return its public URL with the process.

    Uses --protocol http2: QUIC (UDP) silently dies behind VPN/NAT,
    TCP mode is stable. The banner URL appears BEFORE edge
    registration, so we additionally wait for the "Registered tunnel
    connection" line — without it the Cloudflare edge answers bare
    404 on every path.

    Args:
        port: Local port the origin service listens on.

    Returns:
        Tuple of (public URL, running cloudflared process).

    Raises:
        RuntimeError: Binary missing, URL timeout or no edge
            registration within the timeout.
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
    logger.info(f"Starting cloudflared (http2) on port {port}...")
    process = _popen(cmd)
    url = _read_url(process)
    if url is None:
        process.terminate()
        raise RuntimeError(
            f"cloudflared produced no URL in {_TIMEOUT}s "
            "(or exited with an error — see logs above)."
        )
    if not _wait_registered(process):
        process.terminate()
        raise RuntimeError(
            f"cloudflared did not register at the edge in {_TIMEOUT}s "
            "— tunnel is not usable."
        )
    logger.info(f"Tunnel ready: {url}")
    time.sleep(2)
    _drain_stdout(process)
    return url, process


def stop_tunnel(process: subprocess.Popen | None) -> None:
    """Terminate the tunnel process on graceful shutdown."""
    if process is None:
        return
    try:
        process.terminate()
        process.wait(timeout=3)
        logger.info("Tunnel stopped.")
    except Exception:
        try:
            process.kill()
            logger.warning("Tunnel force-killed.")
        except Exception as exc:
            logger.error(f"Failed to terminate tunnel: {exc}")


def _popen(cmd: list[str]) -> subprocess.Popen:
    """Spawn a process with a clear error when binary is missing.

    Args:
        cmd: Command line to execute.

    Returns:
        Started subprocess with piped stdout.

    Raises:
        RuntimeError: The executable was not found.
    """
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
            f"Binary not found: {cmd[0]}. Install cloudflared "
            "or set TELEGRAM_WEBHOOK_URL."
        ) from exc


def _read_url(process: subprocess.Popen) -> str | None:
    """Read tunnel JSON logs until a public URL appears.

    Args:
        process: Running cloudflared subprocess.

    Returns:
        Public URL, or None on timeout/EOF.
    """
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
        logger.debug(f"[cloudflared] {line[:200]}")
    return None


def _wait_registered(process: subprocess.Popen, timeout: int = 20) -> bool:
    """Wait until the tunnel is actually registered at the edge.

    Args:
        process: Running cloudflared subprocess.
        timeout: Max seconds to wait for registration.

    Returns:
        True once "Registered tunnel connection" is seen.
    """
    started_at = time.monotonic()

    assert process.stdout is not None
    for line in process.stdout:
        if time.monotonic() - started_at > timeout:
            return False
        line = line.strip()
        if not line:
            continue
        logger.debug(f"[cloudflared] {line[:200]}")
        if "Registered tunnel connection" in line:
            return True
        if '"level":"fatal"' in line or '"level":"error"' in line:
            logger.error(f"[cloudflared] {line[:200]}")
    return False


def _drain_stdout(process: subprocess.Popen) -> None:
    """Hand the pipe to a daemon thread that keeps draining it.

    Without this the OS pipe buffer fills and the tunnel freezes.
    """
    thread = threading.Thread(
        target=_drain_loop,
        args=(process,),
        name="cloudflared-drain",
        daemon=True,
    )
    thread.start()


def _drain_loop(process: subprocess.Popen) -> None:
    """Consume stdout forever, relaying lines at DEBUG level."""
    assert process.stdout is not None
    for line in process.stdout:
        line = line.strip()
        if line:
            logger.debug(f"[cloudflared] {line[:200]}")
