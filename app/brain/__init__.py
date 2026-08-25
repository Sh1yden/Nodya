"""The brain of Nodya: models, memory, LLM, repositories, skills.

Exposes the system-prompt loader as its public facade.
"""

from .memory.init import load_system_prompt

__all__ = ["load_system_prompt"]
