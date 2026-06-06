"""Token usage quota checker for API keys."""

from datetime import datetime, timezone

from app.database import get_db


async def check_quota(user_name: str, daily_quota: int, monthly_quota: int) -> str | None:
    """Check if user has exceeded token quota. Returns error message or None.

    Args:
        user_name: API key owner name.
        daily_quota: Max tokens per day (0 = unlimited).
        monthly_quota: Max tokens per month (0 = unlimited).
    """
    if daily_quota <= 0 and monthly_quota <= 0:
        return None

    db = await get_db()
    now = datetime.now(timezone.utc)

    # Daily check
    if daily_quota > 0:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        cursor = await db.execute(
            "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) as total "
            "FROM request_logs WHERE user_name = ? AND created_at >= ?",
            (user_name, day_start),
        )
        row = await cursor.fetchone()
        if row and row["total"] >= daily_quota:
            return f"Daily token quota exceeded ({row['total']}/{daily_quota})"

    # Monthly check
    if monthly_quota > 0:
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        cursor = await db.execute(
            "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) as total "
            "FROM request_logs WHERE user_name = ? AND created_at >= ?",
            (user_name, month_start),
        )
        row = await cursor.fetchone()
        if row and row["total"] >= monthly_quota:
            return f"Monthly token quota exceeded ({row['total']}/{monthly_quota})"

    return None
