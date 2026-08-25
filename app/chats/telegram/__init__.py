"""Telegram channel: webhook intake and delivery consumer."""

from .sender import TGSender
from .webhook import router

__all__ = ["TGSender", "router"]
