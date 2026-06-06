from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import structlog

import aiosqlite

from app.config import AppConfig

_db: Optional[aiosqlite.Connection] = None


async def init_db(config: AppConfig) -> aiosqlite.Connection:
    global _db
    db_path = Path(config.database.path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(str(db_path))
    _db.row_factory = aiosqlite.Row
    # Enable WAL mode for better concurrent read/write performance
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _init_tables(_db)
    return _db


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


async def _init_tables(db: aiosqlite.Connection):
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key         TEXT    UNIQUE NOT NULL,
            name        TEXT    NOT NULL,
            priority    INTEGER NOT NULL DEFAULT 100,
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS request_logs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id        TEXT    UNIQUE NOT NULL,
            user_name         TEXT,
            endpoint          TEXT    NOT NULL,
            model             TEXT,
            priority          INTEGER NOT NULL,
            enqueue_time      TIMESTAMP,
            dequeue_time      TIMESTAMP,
            complete_time     TIMESTAMP,
            wait_time_ms      INTEGER,
            processing_time_ms INTEGER,
            status_code       INTEGER,
            streamed          INTEGER,
            prompt_tokens     INTEGER,
            completion_tokens INTEGER,
            error             TEXT,
            client_ip         TEXT,
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Migration: add token columns for existing databases
    for col in ("prompt_tokens", "completion_tokens"):
        try:
            await db.execute(f"ALTER TABLE request_logs ADD COLUMN {col} INTEGER")
        except Exception:
            pass
    # Migration: add rate_limit column for existing databases
    try:
        await db.execute("ALTER TABLE api_keys ADD COLUMN rate_limit INTEGER DEFAULT 0")
    except Exception:
        pass
    # Migration: add token quota columns for existing databases
    for col in ("token_quota_daily", "token_quota_monthly"):
        try:
            await db.execute(f"ALTER TABLE api_keys ADD COLUMN {col} INTEGER DEFAULT 0")
        except Exception:
            pass

    # Indexes for request_logs (IF NOT EXISTS for idempotency)
    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_logs_created_at ON request_logs(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_logs_user ON request_logs(user_name)",
        "CREATE INDEX IF NOT EXISTS idx_logs_endpoint ON request_logs(endpoint)",
        "CREATE INDEX IF NOT EXISTS idx_keys_key ON api_keys(key)",
    ):
        try:
            await db.execute(idx_sql)
        except Exception as e:
            logger = structlog.get_logger()
            logger.warning("db.migration.failed", sql=idx_sql, error=str(e))

    await db.commit()


async def get_db() -> aiosqlite.Connection:
    assert _db is not None, "Database not initialized"
    return _db


async def cleanup_old_logs(retention_days: int = 90, max_records: int = 100_000):
    """Delete log records older than retention_days, then trim to max_records."""
    db = await get_db()
    import structlog
    logger = structlog.get_logger()

    if retention_days > 0:
        cutoff = datetime.now(timezone.utc).isoformat()
        cursor = await db.execute(
            "SELECT COUNT(*) as c FROM request_logs "
            "WHERE created_at < datetime(?, '-' || ? || ' days')",
            (cutoff, str(retention_days)),
        )
        old_count = (await cursor.fetchone())["c"]
        if old_count > 0:
            await db.execute(
                "DELETE FROM request_logs "
                "WHERE created_at < datetime(?, '-' || ? || ' days')",
                (cutoff, str(retention_days)),
            )
            await db.commit()
            logger.info("log.cleanup.retention", deleted=old_count,
                        retention_days=retention_days)

    if max_records > 0:
        cursor = await db.execute("SELECT COUNT(*) as c FROM request_logs")
        total = (await cursor.fetchone())["c"]
        if total > max_records:
            excess = total - max_records
            await db.execute(
                "DELETE FROM request_logs WHERE id IN "
                "(SELECT id FROM request_logs ORDER BY created_at ASC LIMIT ?)",
                (excess,),
            )
            await db.commit()
            logger.info("log.cleanup.max_records", deleted=excess,
                        max_records=max_records)
