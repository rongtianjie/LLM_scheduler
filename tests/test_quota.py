"""Token quota checker tests."""

from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_quota_both_zero_returns_none(db):
    """When both quotas are 0, check_quota returns None (unlimited)."""
    from app.core.quota_checker import check_quota
    result = await check_quota("testuser", 0, 0)
    assert result is None


@pytest.mark.asyncio
async def test_quota_daily_exceeded(db):
    """When daily quota is exceeded, an error message is returned."""
    from app.core.quota_checker import check_quota

    # Insert a log entry that exceeds the daily quota
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO request_logs (request_id, user_name, endpoint, model, priority, "
        "prompt_tokens, completion_tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("req-1", "testuser", "/v1/chat/completions", "gpt-4", 100, 80, 30, now),
    )
    await db.commit()

    # Quota is 100, used 80+30=110 → exceeded
    result = await check_quota("testuser", 100, 0)
    assert result is not None
    assert "Daily token quota exceeded" in result
    assert "110/100" in result


@pytest.mark.asyncio
async def test_quota_daily_under_limit(db):
    """When daily quota is not exceeded, returns None."""
    from app.core.quota_checker import check_quota

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO request_logs (request_id, user_name, endpoint, model, priority, "
        "prompt_tokens, completion_tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("req-1", "testuser", "/v1/chat/completions", "gpt-4", 100, 10, 20, now),
    )
    await db.commit()

    result = await check_quota("testuser", 100, 0)
    assert result is None


@pytest.mark.asyncio
async def test_quota_monthly_exceeded(db):
    """When monthly quota is exceeded, an error message is returned."""
    from app.core.quota_checker import check_quota

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO request_logs (request_id, user_name, endpoint, model, priority, "
        "prompt_tokens, completion_tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("req-1", "testuser", "/v1/chat/completions", "gpt-4", 100, 5000, 5000, now),
    )
    await db.commit()

    result = await check_quota("testuser", 0, 10000)
    assert result is not None
    assert "Monthly token quota exceeded" in result


@pytest.mark.asyncio
async def test_quota_isolated_per_user(db):
    """Quota check is per-user; one user's usage doesn't affect another."""
    from app.core.quota_checker import check_quota

    now = datetime.now(timezone.utc).isoformat()
    # user_a exceeds quota
    await db.execute(
        "INSERT INTO request_logs (request_id, user_name, endpoint, model, priority, "
        "prompt_tokens, completion_tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("req-1", "user_a", "/v1/chat/completions", "gpt-4", 100, 500, 600, now),
    )
    await db.commit()

    result_a = await check_quota("user_a", 1000, 0)
    assert result_a is not None  # exceeded

    result_b = await check_quota("user_b", 1000, 0)
    assert result_b is None  # different user, no usage


@pytest.mark.asyncio
async def test_quota_with_null_tokens(db):
    """Log entries with NULL token columns should not break quota check."""
    from app.core.quota_checker import check_quota

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO request_logs (request_id, user_name, endpoint, model, priority, "
        "prompt_tokens, completion_tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("req-1", "testuser", "/v1/chat/completions", "gpt-4", 100, None, None, now),
    )
    await db.commit()

    result = await check_quota("testuser", 1, 0)
    assert result is None  # NULL tokens sum to 0 via COALESCE
