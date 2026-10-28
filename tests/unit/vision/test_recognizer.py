"""Unit tests for ``app.brain.vision.recognizer``."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from app.brain.llm_choice.base import LLMResponse
from app.brain.vision import MediaRecognizer
from app.common import IncomingMessage, MediaDescriptor


def _dto(
    text: str = "",
    media: MediaDescriptor | None = None,
    media_type: str | None = None,
    media_file_id: str | None = None,
) -> IncomingMessage:
    return IncomingMessage(
        user_external_id="1",
        channel="telegram",
        text=text,
        received_at=datetime.now(UTC),
        media=media,
        media_type=media_type,  # type: ignore[arg-type]
        media_file_id=media_file_id,
    )


def _recognizer(
    router,
    fetch,
    *,
    max_image_bytes: int = 1024,
    max_chars: int = 200,
    max_per_batch: int = 10,
) -> MediaRecognizer:
    return MediaRecognizer(
        router=router,
        fetch_media=fetch,
        max_image_bytes=max_image_bytes,
        max_chars=max_chars,
        max_per_batch=max_per_batch,
    )


def _photo(file_id: str = "photo-1") -> MediaDescriptor:
    return MediaDescriptor(
        kind="photo", file_id=file_id, width=800, height=600
    )


class TestDescribe:
    """Single media descriptions and marker formatting."""

    async def test_photo_uses_main_file_and_mime(
        self, mock_llm_router
    ) -> None:
        fetch = AsyncMock(return_value=(b"img", "image/jpeg"))
        recognizer = _recognizer(mock_llm_router, fetch)

        marker = await recognizer.describe(_dto(media=_photo()))

        assert marker == "[photo: Hello from LLM; 800x600]"
        fetch.assert_awaited_once_with("photo-1")
        role = mock_llm_router.generate_with_fallback.await_args.args[0]
        assert role == "media"
        chat = mock_llm_router.generate_with_fallback.await_args.args[1]
        image_part, text_part = chat[0].content
        assert image_part.type == "image_url"
        assert image_part.inline_data == base64.b64encode(b"img").decode()
        assert image_part.mime_type == "image/jpeg"
        assert text_part.type == "text"

    async def test_animation_uses_thumbnail(self, mock_llm_router) -> None:
        fetch = AsyncMock(return_value=(b"gif", "image/jpeg"))
        recognizer = _recognizer(mock_llm_router, fetch)
        media = MediaDescriptor(
            kind="animation",
            file_id="gif-main",
            thumbnail_file_id="gif-thumb",
            duration_seconds=3,
            width=480,
            height=270,
        )

        marker = await recognizer.describe(_dto(media=media))

        assert marker == "[gif: Hello from LLM; 3s, 480x270]"
        fetch.assert_awaited_once_with("gif-thumb")

    async def test_static_sticker_uses_own_file(self, mock_llm_router) -> None:
        fetch = AsyncMock(return_value=(b"webp", "image/webp"))
        recognizer = _recognizer(mock_llm_router, fetch)
        media = MediaDescriptor(
            kind="sticker",
            file_id="sticker-main",
            sticker_format="static",
            emoji="😂",
            sticker_set_name="cats",
        )

        marker = await recognizer.describe(_dto(media=media))

        assert marker is not None
        assert marker.startswith("[sticker: Hello from LLM; static")
        assert "emoji 😂" in marker
        assert "set cats" in marker
        fetch.assert_awaited_once_with("sticker-main")

    async def test_animated_sticker_uses_thumbnail(
        self, mock_llm_router
    ) -> None:
        fetch = AsyncMock(return_value=(b"webp", "image/webp"))
        recognizer = _recognizer(mock_llm_router, fetch)
        media = MediaDescriptor(
            kind="sticker",
            file_id="sticker-main",
            thumbnail_file_id="sticker-thumb",
            sticker_format="animated",
        )

        await recognizer.describe(_dto(media=media))

        fetch.assert_awaited_once_with("sticker-thumb")

    async def test_voice_is_metadata_only(self, mock_llm_router) -> None:
        fetch = AsyncMock()
        recognizer = _recognizer(mock_llm_router, fetch)
        media = MediaDescriptor(
            kind="voice", file_id="voice-1", duration_seconds=7
        )

        marker = await recognizer.describe(_dto(media=media))

        assert marker == "[voice: 7s]"
        fetch.assert_not_awaited()
        mock_llm_router.generate_with_fallback.assert_not_awaited()

    async def test_audio_duration_format(self, mock_llm_router) -> None:
        fetch = AsyncMock()
        recognizer = _recognizer(mock_llm_router, fetch)
        media = MediaDescriptor(
            kind="audio", file_id="audio-1", duration_seconds=225
        )

        marker = await recognizer.describe(_dto(media=media))

        assert marker == "[audio: 3:45]"

    async def test_oversize_primary_falls_back_to_thumbnail(
        self, mock_llm_router
    ) -> None:
        async def fetch(file_id: str) -> tuple[bytes, str]:
            if file_id == "photo-main":
                return b"x" * 2048, "image/jpeg"
            return b"ok", "image/jpeg"

        recognizer = _recognizer(mock_llm_router, AsyncMock(side_effect=fetch))
        media = MediaDescriptor(
            kind="photo",
            file_id="photo-main",
            thumbnail_file_id="photo-thumb",
        )

        marker = await recognizer.describe(_dto(media=media))

        assert marker is not None
        assert "Hello from LLM" in marker

    async def test_non_image_primary_falls_back_to_thumbnail(
        self, mock_llm_router
    ) -> None:
        async def fetch(file_id: str) -> tuple[bytes, str]:
            if file_id == "main":
                return b"raw", "video/mp4"
            return b"img", "image/jpeg"

        recognizer = _recognizer(mock_llm_router, AsyncMock(side_effect=fetch))
        media = MediaDescriptor(
            kind="photo", file_id="main", thumbnail_file_id="thumb"
        )

        marker = await recognizer.describe(_dto(media=media))

        assert marker is not None
        assert "Hello from LLM" in marker

    async def test_video_without_thumbnail_reports_no_preview(
        self, mock_llm_router
    ) -> None:
        fetch = AsyncMock()
        recognizer = _recognizer(mock_llm_router, fetch)
        media = MediaDescriptor(
            kind="video",
            file_id="video-1",
            duration_seconds=12,
            width=1280,
            height=720,
        )

        marker = await recognizer.describe(_dto(media=media))

        assert marker == "[video: превью недоступно; 12s, 1280x720]"
        fetch.assert_not_awaited()

    async def test_unavailable_preview_is_reported(
        self, mock_llm_router
    ) -> None:
        fetch = AsyncMock(side_effect=RuntimeError("expired"))
        recognizer = _recognizer(mock_llm_router, fetch)

        marker = await recognizer.describe(_dto(media=_photo()))

        assert marker == "[photo: превью недоступно; 800x600]"
        mock_llm_router.generate_with_fallback.assert_not_awaited()

    async def test_llm_failure_degrades_to_marker(
        self, mock_llm_router
    ) -> None:
        fetch = AsyncMock(return_value=(b"img", "image/jpeg"))
        mock_llm_router.generate_with_fallback.side_effect = RuntimeError(
            "no provider"
        )
        recognizer = _recognizer(mock_llm_router, fetch)

        marker = await recognizer.describe(_dto(media=_photo()))

        assert marker == "[photo: не удалось распознать; 800x600]"

    async def test_description_is_cleaned_and_truncated(
        self, mock_llm_router
    ) -> None:
        fetch = AsyncMock(return_value=(b"img", "image/jpeg"))
        mock_llm_router.generate_with_fallback.return_value = LLMResponse(
            text="  кот [спит]\n на диване  "
        )
        recognizer = _recognizer(mock_llm_router, fetch, max_chars=10)

        marker = await recognizer.describe(_dto(media=_photo()))

        assert marker == "[photo: кот (спит); 800x600]"

    async def test_legacy_dto_is_supported(self, mock_llm_router) -> None:
        fetch = AsyncMock(return_value=(b"img", "image/jpeg"))
        recognizer = _recognizer(mock_llm_router, fetch)
        dto = _dto(media_type="photo", media_file_id="legacy-file")

        marker = await recognizer.describe(dto)

        assert marker == "[photo: Hello from LLM]"
        fetch.assert_awaited_once_with("legacy-file")

    async def test_no_media_returns_none(self, mock_llm_router) -> None:
        recognizer = _recognizer(mock_llm_router, AsyncMock())

        assert await recognizer.describe(_dto(text="привет")) is None


class TestDescribeMany:
    """Batch alignment and limits."""

    async def test_result_is_aligned_with_inputs(
        self, mock_llm_router
    ) -> None:
        fetch = AsyncMock(return_value=(b"img", "image/jpeg"))
        recognizer = _recognizer(mock_llm_router, fetch)

        results = await recognizer.describe_many(
            [_dto(text="привет"), _dto(media=_photo())]
        )

        assert results[0] is None
        assert results[1] == "[photo: Hello from LLM; 800x600]"

    async def test_batch_limit_skips_extra_media(
        self, mock_llm_router
    ) -> None:
        fetch = AsyncMock(return_value=(b"img", "image/jpeg"))
        recognizer = _recognizer(mock_llm_router, fetch, max_per_batch=1)

        results = await recognizer.describe_many(
            [_dto(media=_photo("a")), _dto(media=_photo("b"))]
        )

        assert results[0] == "[photo: Hello from LLM; 800x600]"
        assert results[1] is not None
        assert "пропущено" in results[1]
        fetch.assert_awaited_once_with("a")
