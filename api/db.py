from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row


def connect(database_url: str, **kwargs: Any) -> psycopg.Connection:
    """Open a PostgreSQL connection with dict rows."""
    return psycopg.connect(database_url, row_factory=dict_row, **kwargs)
