"""The brain of Nodya: models, memory, LLM, repositories, skills.

Exposes the system-prompt helpers as its public facade.
"""

from .memory.init import build_interlocutor_block, load_system_prompt

__all__ = ["build_interlocutor_block", "load_system_prompt"]
