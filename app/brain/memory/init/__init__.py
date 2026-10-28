"""Nodya identity init: ME/RULES/CREATOR prompts and interlocutor block."""

from .prompts import (
    PROMPTS_DIR,
    build_interlocutor_block,
    load_system_prompt,
)

__all__ = [
    "PROMPTS_DIR",
    "build_interlocutor_block",
    "load_system_prompt",
]
