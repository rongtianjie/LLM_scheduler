from abc import ABC, abstractmethod
from typing import AsyncGenerator, Union

from app.config import BackendConfig
from app.models import RequestContext


class BaseAdapter(ABC):
    """Abstract base adapter for LLM backend communication."""

    def __init__(self, backend_config: BackendConfig):
        self._config = backend_config

    @property
    def config(self) -> BackendConfig:
        return self._config

    @abstractmethod
    async def stream(self, context: RequestContext) -> AsyncGenerator[bytes, None]:
        """Stream the response from the backend (SSE passthrough)."""
        ...

    @abstractmethod
    async def call(self, context: RequestContext) -> Union[dict, bytes]:
        """Non-streaming call to the backend."""
        ...
