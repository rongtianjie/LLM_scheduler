import json
from typing import AsyncGenerator, Union

import httpx

from app.adapters.base import BaseAdapter
from app.models import RequestContext


def _parse_token_chunk(chunk: bytes, context: RequestContext):
    """Try to extract token usage from an SSE chunk (OpenAI format)."""
    try:
        text = chunk.decode("utf-8", errors="replace")
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("data: ") and line != "data: [DONE]":
                data = json.loads(line[6:])
                usage = data.get("usage")
                if usage:
                    context.prompt_tokens = usage.get("prompt_tokens", context.prompt_tokens)
                    context.completion_tokens = usage.get("completion_tokens", context.completion_tokens)
    except Exception:
        pass


class OpenAIAdapter(BaseAdapter):
    """Adapter for OpenAI-compatible /chat/completions endpoint."""

    PATH = "/chat/completions"

    def __init__(self, backend_config, proxy_url: str = ""):
        super().__init__(backend_config, proxy_url)

    async def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    async def stream(self, context: RequestContext) -> AsyncGenerator[bytes, None]:
        url = f"{self.config.base_url}{self.PATH}"
        async with httpx.AsyncClient(timeout=self.config.timeout,
                                     trust_env=False,
                                     proxy=self._proxy_url or None) as client:
            try:
                async with client.stream("POST", url, json=context.body,
                                         headers=await self._headers()) as resp:
                    context.response_status = resp.status_code
                    if resp.status_code != 200:
                        yield await resp.aread()
                        return
                    async for chunk in resp.aiter_bytes():
                        _parse_token_chunk(chunk, context)
                        yield chunk
            except (httpx.TimeoutException):
                context.response_status = 504
                context.error = "Backend timeout"
                yield json.dumps({"error": "Backend timeout"}).encode()
            except (httpx.ConnectError, OSError):
                context.response_status = 502
                context.error = "Backend connection failed"
                yield json.dumps({"error": "Backend connection failed"}).encode()

    async def call(self, context: RequestContext) -> Union[dict, bytes]:
        url = f"{self.config.base_url}{self.PATH}"
        async with httpx.AsyncClient(timeout=self.config.timeout,
                                     trust_env=False,
                                     proxy=self._proxy_url or None) as client:
            try:
                resp = await client.post(url, json=context.body,
                                         headers=await self._headers())
                context.response_status = resp.status_code
                if resp.status_code != 200:
                    context.error = f"Backend returned {resp.status_code}"
                    return resp.content
                data = resp.json()
                usage = data.get("usage", {})
                if usage:
                    context.prompt_tokens = usage.get("prompt_tokens")
                    context.completion_tokens = usage.get("completion_tokens")
                return data
            except (httpx.TimeoutException):
                context.response_status = 504
                context.error = "Backend timeout"
                return {"error": "Backend timeout"}
            except (httpx.ConnectError, OSError):
                context.response_status = 502
                context.error = "Backend connection failed"
                return {"error": "Backend connection failed"}
