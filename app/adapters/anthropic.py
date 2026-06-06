import json
from typing import AsyncGenerator, Union

import httpx

from app.adapters.base import BaseAdapter
from app.models import RequestContext


def _parse_token_chunk(chunk: bytes, context: RequestContext):
    """Try to extract token usage from an SSE chunk (Anthropic format).

    input_tokens come from the message_start event,
    output_tokens from the message_delta event.
    """
    try:
        text = chunk.decode("utf-8", errors="replace")
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                data = json.loads(line[6:])
                event_type = data.get("type")
                if event_type == "message_start":
                    msg = data.get("message", {})
                    usage = msg.get("usage", {})
                    if usage.get("input_tokens") is not None:
                        context.prompt_tokens = usage["input_tokens"]
                elif event_type == "message_delta":
                    usage = data.get("usage", {})
                    if usage.get("output_tokens") is not None:
                        context.completion_tokens = usage["output_tokens"]
    except Exception:
        pass


class AnthropicAdapter(BaseAdapter):
    """Adapter for Anthropic-compatible /messages endpoint."""

    PATH = "/messages"

    def __init__(self, backend_config, proxy_url: str = ""):
        super().__init__(backend_config, proxy_url)

    async def _headers(self) -> dict:
        headers = {
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        if self.config.api_key:
            headers["x-api-key"] = self.config.api_key
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
            except httpx.TimeoutException:
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
                if usage.get("input_tokens") is not None:
                    context.prompt_tokens = usage["input_tokens"]
                if usage.get("output_tokens") is not None:
                    context.completion_tokens = usage["output_tokens"]
                return data
            except httpx.TimeoutException:
                context.response_status = 504
                context.error = "Backend timeout"
                return {"error": "Backend timeout"}
            except (httpx.ConnectError, OSError):
                context.response_status = 502
                context.error = "Backend connection failed"
                return {"error": "Backend connection failed"}
