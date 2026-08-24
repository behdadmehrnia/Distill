from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


ROLE_ADMIN = "admin"
ROLE_USER = "user"
VALID_ROLES = frozenset({ROLE_ADMIN, ROLE_USER})


@dataclass
class UserRecord:
    id: str
    email: str
    password_hash: str
    display_name: str = ""
    created_at: float = field(default_factory=time.time)
    is_active: bool = True
    role: str = ROLE_USER

    @staticmethod
    def create(
        email: str,
        password_hash: str,
        display_name: str = "",
        role: str = ROLE_USER,
    ) -> "UserRecord":
        return UserRecord(
            id=str(uuid.uuid4()),
            email=email.strip().lower(),
            password_hash=password_hash,
            display_name=display_name.strip(),
            role=role if role in VALID_ROLES else ROLE_USER,
        )

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name or self.email.split("@")[0],
            "created_at": self.created_at,
            "role": self.role,
            "is_active": self.is_active,
        }
