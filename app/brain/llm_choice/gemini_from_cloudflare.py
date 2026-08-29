from .base import LLMProvider


_API_URL = ""


class GeminiCloudflare(LLMProvider):
    """Primary provider operates via its own domain
    and a worker located on Cloudflare,
    because Gemini now treats the VPN address as a datacenter."""

    def __init__(self) -> None:
        pass
