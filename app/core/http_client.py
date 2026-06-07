"""Shared httpx AsyncClient with connection-pool reuse."""

from typing import Optional

import httpx


_client: Optional[httpx.AsyncClient] = None
_current_proxy_url: str = ""


async def init_client(proxy_url: str = ""):
    """Create or recreate the shared HTTP client."""
    global _client, _current_proxy_url
    if _client:
        await _client.aclose()
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
    _client = httpx.AsyncClient(
        limits=limits,
        trust_env=False,
        proxy=proxy_url or None,
    )
    _current_proxy_url = proxy_url


async def get_client(timeout: float = 300, proxy_url: str = "") -> httpx.AsyncClient:
    """Return the shared AsyncClient, recreating if proxy URL changed.

    Args:
        timeout: Per-request timeout in seconds.
        proxy_url: Optional proxy URL; if changed, the client is rebuilt.
    """
    global _client, _current_proxy_url
    if _client is None or _client.is_closed:
        await init_client(proxy_url)
    elif proxy_url and proxy_url != _current_proxy_url:
        await init_client(proxy_url)
    # Dynamically adjust timeout for this request
    _client.timeout = httpx.Timeout(timeout)
    return _client


async def close_client():
    """Close and clean up the shared HTTP client."""
    global _client
    if _client:
        await _client.aclose()
        _client = None
