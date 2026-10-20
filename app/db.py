from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .utils import now_iso

_db_path: Path | None = None
_init_lock = threading.Lock()


def configure(path: Path) -> None:
    global _db_path
    _db_path = Path(path).resolve()
    _db_path.parent.mkdir(parents=True, exist_ok=True)


def get_path() -> Path:
    if _db_path is None:
        raise RuntimeError("Database is not configured")
    return _db_path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = get_path()
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _init_lock, connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                repo_path TEXT NOT NULL,
                default_branch TEXT NOT NULL DEFAULT 'main',
                verify_command TEXT NOT NULL DEFAULT '',
                publish_command TEXT NOT NULL DEFAULT '',
                allowed_paths_json TEXT NOT NULL DEFAULT '["."]',
                auto_apply INTEGER NOT NULL DEFAULT 0,
                auto_publish INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS requirements (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                raw_input TEXT NOT NULL,
                spec_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                requirement_id TEXT NOT NULL REFERENCES requirements(id) ON DELETE CASCADE,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'queued',
                stage TEXT NOT NULL DEFAULT 'intake',
                clarify_json TEXT NOT NULL DEFAULT '{}',
                answers_json TEXT NOT NULL DEFAULT '{}',
                clarify_round INTEGER NOT NULL DEFAULT 0,
                feasibility_json TEXT NOT NULL DEFAULT '{}',
                context_json TEXT NOT NULL DEFAULT '{}',
                plan_json TEXT NOT NULL DEFAULT '{}',
                plan_approved INTEGER NOT NULL DEFAULT 0,
                code_json TEXT NOT NULL DEFAULT '{}',
                review_json TEXT NOT NULL DEFAULT '{}',
                verify_json TEXT NOT NULL DEFAULT '{}',
                deliver_json TEXT NOT NULL DEFAULT '{}',
                distill_json TEXT NOT NULL DEFAULT '{}',
                patch_text TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                branch_name TEXT NOT NULL DEFAULT '',
                worktree_path TEXT NOT NULL DEFAULT '',
                llm_provider TEXT NOT NULL DEFAULT '',
                llm_model TEXT NOT NULL DEFAULT '',
                tokens_in INTEGER NOT NULL DEFAULT 0,
                tokens_out INTEGER NOT NULL DEFAULT 0,
                rework_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                stage TEXT NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knowledge (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 0.5,
                ttl_days INTEGER NOT NULL DEFAULT 90,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'accepted',
                source_run_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS knowledge_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_id TEXT NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
                run_id TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knowledge_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_id TEXT NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
                decision TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id, id);
            CREATE INDEX IF NOT EXISTS idx_knowledge_project ON knowledge(project_id, status, expires_at);
            CREATE INDEX IF NOT EXISTS idx_knowledge_reviews_item ON knowledge_reviews(knowledge_id, id);
            CREATE INDEX IF NOT EXISTS idx_requirements_project ON requirements(project_id, created_at);
            """
        )


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    with connect() as conn:
        cur = conn.execute(sql, params)
        return int(cur.lastrowid or 0)


def executemany(sql: str, params: Iterable[Sequence[Any]]) -> None:
    with connect() as conn:
        conn.executemany(sql, params)


def query_one(sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(sql, params).fetchone()


def query_all(sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
    with connect() as conn:
        return list(conn.execute(sql, params).fetchall())


def update(table: str, row_id: str, **fields: Any) -> int:
    if not fields:
        return 0
    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [row_id]
    with connect() as conn:
        cur = conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", values)
        return int(cur.rowcount)
