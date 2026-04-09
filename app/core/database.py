from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(source_path, file_hash)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_created_at
ON jobs(status, created_at DESC);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    sequence_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    delivery_status TEXT NOT NULL,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    title TEXT,
    body TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    delivered_at TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    UNIQUE(job_id, sequence_no)
);

CREATE INDEX IF NOT EXISTS idx_articles_job_id
ON articles(job_id, sequence_no ASC);

CREATE INDEX IF NOT EXISTS idx_articles_status
ON articles(status, delivery_status);
"""


class AppDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def session(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.session() as connection:
            connection.executescript(SCHEMA_SQL)

    def ping(self) -> bool:
        try:
            with self.session() as connection:
                connection.execute("SELECT 1")
            return True
        except sqlite3.Error:
            return False
