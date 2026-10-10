import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def database_path() -> str:
    return os.environ.get("CERTIFICATE_DB_PATH", "certificates.db")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = database_path()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                course_name TEXT NOT NULL,
                completion_date TEXT NOT NULL,
                issuer TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('queued', 'running', 'completed', 'completed_with_errors')
                ),
                total INTEGER NOT NULL,
                succeeded INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS certificates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                recipient_name TEXT NOT NULL,
                recipient_email TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('queued', 'succeeded', 'failed')),
                error TEXT,
                pdf BLOB,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_certificates_job_id
                ON certificates(job_id);
            """
        )
