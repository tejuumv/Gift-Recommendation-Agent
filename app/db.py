import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


class ContactRepository:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        with self._connection:
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS contacts (
                    thread_id TEXT PRIMARY KEY,
                    contact_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    state_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )

    def create(self, thread_id: str, contact_name: str, state: dict[str, Any]) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO contacts VALUES (?, ?, ?, ?, ?, ?)",
                (thread_id, contact_name, "processing", json.dumps(state), now, now),
            )

    def update(self, thread_id: str, status: str, state: dict[str, Any]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE contacts SET status=?, state_json=?, updated_at=? WHERE thread_id=?",
                (status, json.dumps(state, default=str), datetime.now(UTC).isoformat(), thread_id),
            )

    def list(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT thread_id AS contact_id, contact_name, status, created_at, updated_at "
            "FROM contacts ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get(self, thread_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM contacts WHERE thread_id=?", (thread_id,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["state"] = json.loads(result.pop("state_json"))
        return result

