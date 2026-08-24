from __future__ import annotations

import threading
from typing import Any, Mapping, Optional

from api.db import connect

from .models import UserRecord


class UserStore:
    """PostgreSQL persistence for application users."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self):
        return connect(self.database_url)

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        email TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        display_name TEXT NOT NULL DEFAULT '',
                        created_at DOUBLE PRECISION NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"
                )
                conn.commit()

    def save_user(self, user: UserRecord) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO users (
                        id, email, password_hash, display_name, created_at, is_active
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        email = EXCLUDED.email,
                        password_hash = EXCLUDED.password_hash,
                        display_name = EXCLUDED.display_name,
                        is_active = EXCLUDED.is_active
                    """,
                    (
                        user.id,
                        user.email,
                        user.password_hash,
                        user.display_name,
                        user.created_at,
                        bool(user.is_active),
                    ),
                )
                conn.commit()

    def get_user_by_id(self, user_id: str) -> Optional[UserRecord]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM users WHERE id = %s", (user_id,)
                ).fetchone()
                if not row:
                    return None
                return self._row_to_user(row)

    def get_user_by_email(self, email: str) -> Optional[UserRecord]:
        normalized = email.strip().lower()
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM users WHERE email = %s", (normalized,)
                ).fetchone()
                if not row:
                    return None
                return self._row_to_user(row)

    @staticmethod
    def _row_to_user(row: Mapping[str, Any]) -> UserRecord:
        return UserRecord(
            id=row["id"],
            email=row["email"],
            password_hash=row["password_hash"],
            display_name=row["display_name"] or "",
            created_at=float(row["created_at"]),
            is_active=bool(row["is_active"]),
        )
