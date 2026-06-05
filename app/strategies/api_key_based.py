from typing import Optional

from fastapi import Request

from app.config import get_config
from app.database import get_db
from app.strategies.base import PriorityStrategy


class ApiKeyPriorityStrategy(PriorityStrategy):
    """Determine priority from the API key's configured priority in the database."""

    async def get_priority(self, request: Request, user_name: Optional[str]) -> int:
        config = get_config()

        # If auth is disabled, fall back to default priority
        if user_name == "anonymous" or not config.auth.enabled:
            return config.priority.default_priority

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return config.priority.default_priority

        api_key = auth_header[7:]
        db = await get_db()
        cursor = await db.execute("SELECT priority FROM api_keys WHERE key = ?", (api_key,))
        row = await cursor.fetchone()
        if row:
            return row["priority"]

        return config.priority.default_priority
