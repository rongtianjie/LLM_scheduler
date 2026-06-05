from pathlib import Path
from typing import Optional

import aiosqlite

from app.config import AppConfig

_db: Optional[aiosqlite.Connection] = None


async def init_db(config: AppConfig) -> aiosqlite.Connection:
    global _db
    db_path = Path(config.database.path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(str(db_path))
    _db.row_factory = aiosqlite.Row
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
            error             TEXT,
            client_ip         TEXT,
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    await db.commit()


async def get_db() -> aiosqlite.Connection:
    assert _db is not None, "Database not initialized"
    return _db
