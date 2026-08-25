from __future__ import annotations

import threading
from typing import Any, List, Mapping, Optional

from api.db import connect

from .models import ROLE_USER, UserRecord


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
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        role TEXT NOT NULL DEFAULT 'user'
                    )
                    """
                )
                # Upgrade path for databases created before the role column existed.
                conn.execute(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user'"
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
                        id, email, password_hash, display_name, created_at, is_active, role
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        email = EXCLUDED.email,
                        password_hash = EXCLUDED.password_hash,
                        display_name = EXCLUDED.display_name,
                        is_active = EXCLUDED.is_active,
                        role = EXCLUDED.role
                    """,
                    (
                        user.id,
                        user.email,
                        user.password_hash,
                        user.display_name,
                        user.created_at,
                        bool(user.is_active),
                        user.role,
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

    def list_users(self) -> List[UserRecord]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM users ORDER BY created_at ASC"
                ).fetchall()
                return [self._row_to_user(row) for row in rows]

    def delete_user(self, user_id: str) -> bool:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
                conn.commit()
                return cur.rowcount > 0

    def has_admin(self) -> bool:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
                ).fetchone()
                return row is not None

    @staticmethod
    def _row_to_user(row: Mapping[str, Any]) -> UserRecord:
        return UserRecord(
            id=row["id"],
            email=row["email"],
            password_hash=row["password_hash"],
            display_name=row["display_name"] or "",
            created_at=float(row["created_at"]),
            is_active=bool(row["is_active"]),
            role=row["role"] or ROLE_USER,
        )
