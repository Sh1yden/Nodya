"""Telegram-канал: webhook приёма и consumer доставки."""

from .sender import TGSender
from .webhook import router

__all__ = ["TGSender", "router"]
