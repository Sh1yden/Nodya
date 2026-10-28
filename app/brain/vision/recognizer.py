"""Turn media attachments into short text notes for the dialogue model.

Bytes are fetched through an injected callable, so the recognizer
stays channel-agnostic: the Telegram module only supplies file ids
and metadata. Video-like kinds (GIF, video, video note, animated
stickers) are represented by their channel thumbnail — full decoding
is intentionally out of scope.
"""

import asyncio
import base64
from collections.abc import Awaitable, Callable

from app.brain.llm_choice import ChatContentPart, ChatMessage, LLMRouter
from app.common import IncomingMessage, MediaDescriptor
from app.core import LoggerMixin

FetchMedia = Callable[[str], Awaitable[tuple[bytes, str]]]


class MediaRecognizer(LoggerMixin):
    """Describe attached images and preview frames via the media role."""

    def __init__(
        self,
        router: LLMRouter,
        fetch_media: FetchMedia,
        *,
        max_image_bytes: int,
        max_chars: int,
        max_per_batch: int = 10,
        max_concurrency: int = 4,
    ) -> None:
        """Bind dependencies and limits.

        Args:
            router: LLM router; descriptions go through the media role.
            fetch_media: Callable returning (bytes, mime) by file id.
            max_image_bytes: Largest payload accepted by the vision model.
            max_chars: Description length cap before it enters prompts.
            max_per_batch: Media items described per debounced batch.
            max_concurrency: Parallel vision calls for albums.
        """
        self._router = router
        self._fetch_media = fetch_media
        self._max_image_bytes = max_image_bytes
        self._max_chars = max_chars
        self._max_per_batch = max_per_batch
        self._max_concurrency = max_concurrency

    async def describe_many(
        self, dtos: list[IncomingMessage]
    ) -> list[str | None]:
        """Describe every attachment of a debounced batch.

        Args:
            dtos: Incoming messages in chronological order.

        Returns:
            One marker per DTO (None when the message has no media),
            aligned with the input list. Never raises.
        """
        descriptors = [self._descriptor(dto) for dto in dtos]
        indices = [
            index
            for index, media in enumerate(descriptors)
            if media is not None
        ]
        allowed = set(indices[: self._max_per_batch])
        results: list[str | None] = [None] * len(dtos)
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def run(index: int) -> None:
            media = descriptors[index]
            if media is None:
                return
            if index not in allowed:
                results[index] = self._marker(
                    media, "пропущено: лимит медиа в батче"
                )
                return
            async with semaphore:
                results[index] = await self._describe_descriptor(media)

        await asyncio.gather(*(run(index) for index in indices))
        return results

    async def describe(self, dto: IncomingMessage) -> str | None:
        """Describe one message's attachment.

        Args:
            dto: Incoming message with an optional media descriptor.

        Returns:
            Marker text, or None when the message carries no media.
        """
        media = self._descriptor(dto)
        if media is None:
            return None
        return await self._describe_descriptor(media)

    async def _describe_descriptor(self, media: MediaDescriptor) -> str:
        """Build the marker for one attachment without raising.

        Args:
            media: Descriptor normalized by the channel module.

        Returns:
            Single-line marker; failures degrade to a fallback note.
        """
        sources = self._image_sources(media)
        if not sources:
            body = (
                None
                if media.kind in ("voice", "audio")
                else "превью недоступно"
            )
            return self._marker(media, body)
        fetched = False
        for file_id in sources:
            image = await self._fetch_image(file_id)
            if image is None:
                continue
            fetched = True
            try:
                response = await self._router.generate_with_fallback(
                    "media", self._vision_chat(media, image)
                )
            except Exception as exc:
                self._lg.warning(
                    f"Media recognition failed for {media.kind}: {exc}"
                )
                return self._marker(media, "не удалось распознать")
            description = self._clean(response.text or "")
            if description:
                return self._marker(media, description)
        fallback = (
            "превью недоступно" if not fetched else "не удалось распознать"
        )
        return self._marker(media, fallback)

    async def _fetch_image(self, file_id: str) -> tuple[str, str] | None:
        """Download an image or thumbnail and encode it for the LLM.

        Args:
            file_id: Channel file id of an image.

        Returns:
            Base64 payload and MIME type, or None when the source is
            unavailable, too large, or not an image.
        """
        try:
            data, mime = await self._fetch_media(file_id)
        except Exception as exc:
            self._lg.warning(f"Media fetch failed for {file_id}: {exc}")
            return None
        if not mime.startswith("image/"):
            self._lg.debug(f"Media source {file_id} is not an image: {mime}")
            return None
        if len(data) > self._max_image_bytes:
            self._lg.debug(
                f"Media source {file_id} too large: {len(data)} bytes"
            )
            return None
        return base64.b64encode(data).decode(), mime

    def _vision_chat(
        self, media: MediaDescriptor, image: tuple[str, str]
    ) -> list[ChatMessage]:
        """Build the multimodal request for one attachment.

        Args:
            media: Descriptor providing type-specific wording.
            image: Base64 payload and MIME type.

        Returns:
            One user message with the image and the instruction.
        """
        payload, mime = image
        return [
            ChatMessage(
                role="user",
                content=[
                    ChatContentPart(
                        type="image_url",
                        inline_data=payload,
                        mime_type=mime,
                    ),
                    ChatContentPart(type="text", text=self._prompt(media)),
                ],
            )
        ]

    @staticmethod
    def _prompt(media: MediaDescriptor) -> str:
        """Type-specific instruction for the vision model."""
        base = (
            "Опиши изображение для собеседника: 1-2 коротких факта — "
            "что видно, люди и объекты, читаемый текст, действие, "
            "настроение. Без markdown, без вступлений, одной строкой, "
            "по-русски."
        )
        if media.kind == "animation":
            return f"{base} Это превью GIF-анимации: опиши, что происходит."
        if media.kind == "sticker":
            emoji = f" {media.emoji}" if media.emoji else ""
            return (
                f"{base} Это стикер{emoji}: опиши, что изображено "
                "и какое настроение он передаёт."
            )
        if media.kind in ("video", "video_note"):
            return f"{base} Это превью видео: опиши кадр и действие."
        return f"{base} Это фотография или картинка."

    def _marker(self, media: MediaDescriptor, body: str | None) -> str:
        """Format one attachment note as ``[kind: body; meta]``.

        Args:
            media: Descriptor providing kind and metadata.
            body: Description or fallback text; None for metadata-only.

        Returns:
            Single-line marker for prompts and dialogue history.
        """
        tag = "gif" if media.kind == "animation" else media.kind
        meta = self._metadata(media)
        inner = "; ".join(part for part in (body, meta) if part)
        return f"[{tag}: {inner or 'без деталей'}]"

    @staticmethod
    def _metadata(media: MediaDescriptor) -> str:
        """Human-readable size, duration and sticker metadata."""
        parts: list[str] = []
        if media.kind == "sticker":
            if media.sticker_format:
                parts.append(media.sticker_format)
            if media.emoji:
                parts.append(f"emoji {media.emoji}")
            if media.sticker_set_name:
                parts.append(f"set {media.sticker_set_name}")
        if media.duration_seconds is not None:
            parts.append(
                MediaRecognizer._format_duration(media.duration_seconds)
            )
        if media.width and media.height:
            parts.append(f"{media.width}x{media.height}")
        return ", ".join(parts)

    @staticmethod
    def _format_duration(seconds: int) -> str:
        """Render seconds as ``12s`` or ``3:45``."""
        if seconds >= 60:
            minutes, rest = divmod(seconds, 60)
            return f"{minutes}:{rest:02d}"
        return f"{seconds}s"

    def _clean(self, text: str) -> str:
        """Flatten model output into one safe marker line."""
        line = " ".join(text.split())
        line = line.replace("[", "(").replace("]", ")")
        return line[: self._max_chars].rstrip()

    @staticmethod
    def _descriptor(dto: IncomingMessage) -> MediaDescriptor | None:
        """Return the structured descriptor or build a legacy one."""
        if dto.media is not None:
            return dto.media
        if dto.media_type is None or dto.media_file_id is None:
            return None
        return MediaDescriptor(kind=dto.media_type, file_id=dto.media_file_id)

    @staticmethod
    def _image_sources(media: MediaDescriptor) -> list[str]:
        """File ids that may yield a usable image, best first.

        Video-like kinds and animated stickers expose only their
        thumbnail: the main TGS/WebM/MP4 file is not an image.
        """
        if media.kind in ("voice", "audio"):
            return []
        sources: list[str] = []
        if media.kind == "sticker":
            if media.sticker_format not in ("animated", "video"):
                sources.append(media.file_id)
        elif media.kind not in ("animation", "video", "video_note"):
            sources.append(media.file_id)
        if media.thumbnail_file_id:
            sources.append(media.thumbnail_file_id)
        return list(dict.fromkeys(sources))
