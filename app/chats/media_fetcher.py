"""Channel-agnostic media fetching (Telegram only for now)."""

import httpx

from app.core import get_logger, settings

logger = get_logger(__name__)

_MAX_MEDIA_BYTES = 20 * 1024 * 1024
_TELEGRAM_FILE_URL = "https://api.telegram.org/file/bot{token}/{path}"
_TELEGRAM_GET_FILE_URL = "https://api.telegram.org/bot{token}/getFile"


async def fetch_telegram_media(file_id: str) -> tuple[bytes, str]:
    """Download Telegram file by file_id.

    Args:
        file_id: Telegram file_id from photo/video/voice.

    Returns:
        Tuple of (bytes, mime_type).

    Raises:
        ValueError: File too large or not found.
        httpx.HTTPError: Network error (transient).
    """
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is empty.")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: getFile to resolve file_path
        resp = await client.get(
            _TELEGRAM_GET_FILE_URL.format(token=token),
            params={"file_id": file_id},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise ValueError(f"getFile failed: {data}")
        file_path = data["result"]["file_path"]
        file_size = data["result"].get("file_size", 0)
        if file_size and file_size > _MAX_MEDIA_BYTES:
            raise ValueError(f"File too large: {file_size} > 20MB")

        # Step 2: download
        file_url = _TELEGRAM_FILE_URL.format(token=token, path=file_path)
        file_resp = await client.get(file_url)
        file_resp.raise_for_status()
        content = file_resp.content
        if len(content) > _MAX_MEDIA_BYTES:
            raise ValueError(f"Downloaded file too large: {len(content)}")

        # Guess MIME
        mime = "image/jpeg"
        if file_path.endswith(".mp4"):
            mime = "video/mp4"
        elif file_path.endswith(".ogg") or file_path.endswith(".oga"):
            mime = "audio/ogg"
        elif file_path.endswith(".mp3"):
            mime = "audio/mpeg"
        elif file_path.endswith(".webp"):
            mime = "image/webp"
        elif file_path.endswith(".png"):
            mime = "image/png"

        logger.debug(f"Fetched Telegram media {file_id}: {len(content)} bytes")
        return content, mime
