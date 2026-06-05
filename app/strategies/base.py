from abc import ABC, abstractmethod
from typing import Optional

from fastapi import Request


class PriorityStrategy(ABC):
    """Abstract strategy for computing request priority (lower = more urgent)."""

    @abstractmethod
    async def get_priority(self, request: Request, user_name: Optional[str]) -> int:
        ...
