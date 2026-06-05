import json
from typing import AsyncGenerator, Union

import httpx

from app.adapters.base import BaseAdapter
from app.models import RequestContext


class AnthropicAdapter(BaseAdapter):
    """Adapter for Anthropic-compatible /messages endpoint."""

    PATH = "/messages"

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
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            try:
                async with client.stream("POST", url, json=context.body,
                                         headers=await self._headers()) as resp:
                    context.response_status = resp.status_code
                    if resp.status_code != 200:
                        yield await resp.aread()
                        return
                    async for chunk in resp.aiter_bytes():
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
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            try:
                resp = await client.post(url, json=context.body,
                                         headers=await self._headers())
                context.response_status = resp.status_code
                if resp.status_code != 200:
                    context.error = f"Backend returned {resp.status_code}"
                    return resp.content
                return resp.json()
            except httpx.TimeoutException:
                context.response_status = 504
                context.error = "Backend timeout"
                return {"error": "Backend timeout"}
            except (httpx.ConnectError, OSError):
                context.response_status = 502
                context.error = "Backend connection failed"
                return {"error": "Backend connection failed"}
