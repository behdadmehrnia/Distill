from __future__ import annotations

import logging

from .models import ROLE_ADMIN, UserRecord
from .security import hash_password, normalize_email
from .store import UserStore

logger = logging.getLogger(__name__)


def ensure_admin_user(
    user_store: UserStore,
    *,
    admin_username: str | None,
    admin_password: str | None,
) -> None:
    """Guarantee an admin account exists, using ADMIN_USERNAME/ADMIN_PASSWORD.

    No-ops once any admin exists, so operators can change the admin's
    password afterward via the app without it being reset on every restart.
    """
    if user_store.has_admin():
        return
    if not admin_username or not admin_password:
        logger.warning(
            "No admin user exists and ADMIN_USERNAME/ADMIN_PASSWORD are not set; "
            "skipping admin bootstrap"
        )
        return

    email = normalize_email(admin_username)
    existing = user_store.get_user_by_email(email)
    if existing:
        existing.role = ROLE_ADMIN
        existing.is_active = True
        user_store.save_user(existing)
        logger.info("Promoted existing user %s to admin", email)
        return

    admin = UserRecord.create(
        email=email,
        password_hash=hash_password(admin_password),
        display_name="Admin",
        role=ROLE_ADMIN,
    )
    user_store.save_user(admin)
    logger.info("Created admin user %s from ADMIN_USERNAME/ADMIN_PASSWORD", email)
