"""Log retention / cleanup tests."""

from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.asyncio
async def test_cleanup_deletes_old_records(db):
    """Records older than retention_days should be deleted."""
    from app.database import cleanup_old_logs

    # Insert records with various ages
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=100)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()

    await db.execute(
        "INSERT INTO request_logs (request_id, user_name, endpoint, model, priority, "
        "prompt_tokens, completion_tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("old-req", "user1", "/v1/chat/completions", "gpt-4", 100, 10, 10, old),
    )
    await db.execute(
        "INSERT INTO request_logs (request_id, user_name, endpoint, model, priority, "
        "prompt_tokens, completion_tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("recent-req", "user2", "/v1/messages", "claude-3", 100, 20, 20, recent),
    )
    await db.commit()

    # Clean up records older than 30 days
    await cleanup_old_logs(retention_days=30, max_records=100_000)

    # Verify: old record removed, recent record kept
    cursor = await db.execute("SELECT request_id FROM request_logs ORDER BY request_id")
    rows = await cursor.fetchall()
    ids = [r["request_id"] for r in rows]
    assert "old-req" not in ids
    assert "recent-req" in ids


@pytest.mark.asyncio
async def test_cleanup_trims_excess_records(db):
    """When count exceeds max_records, oldest records should be trimmed."""
    from app.database import cleanup_old_logs

    now = datetime.now(timezone.utc).isoformat()
    # Insert 10 records
    for i in range(10):
        await db.execute(
            "INSERT INTO request_logs (request_id, user_name, endpoint, model, priority, "
            "prompt_tokens, completion_tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"req-{i}", "user", "/v1/chat/completions", "gpt-4", 100, 1, 1, now),
        )
    await db.commit()

    # Keep only 5 most recent
    await cleanup_old_logs(retention_days=0, max_records=5)

    cursor = await db.execute("SELECT COUNT(*) as c FROM request_logs")
    count = (await cursor.fetchone())["c"]
    assert count == 5

    # The oldest ones (req-0..req-4) should be gone
    cursor = await db.execute("SELECT request_id FROM request_logs ORDER BY id")
    remaining = [r["request_id"] for r in await cursor.fetchall()]
    assert all(f"req-{i}" not in remaining for i in range(5))
    assert all(f"req-{i}" in remaining for i in range(5, 10))
