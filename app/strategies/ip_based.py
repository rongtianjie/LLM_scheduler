import ipaddress
from typing import Optional

from fastapi import Request

from app.config import get_config
from app.strategies.base import PriorityStrategy


class IPPriorityStrategy(PriorityStrategy):
    """Determine priority from the client IP address using config ip_mapping."""

    async def get_priority(self, request: Request, user_name: Optional[str]) -> int:
        config = get_config()
        client_ip = request.client.host if request.client else "127.0.0.1"

        for ip_pattern, priority in config.priority.ip_mapping.items():
            if self._ip_matches(client_ip, ip_pattern):
                return priority

        return config.priority.default_priority

    def _ip_matches(self, client_ip: str, pattern: str) -> bool:
        try:
            # CIDR notation
            network = ipaddress.ip_network(pattern, strict=False)
            return ipaddress.ip_address(client_ip) in network
        except ValueError:
            # Exact match
            return client_ip == pattern
