from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth.deps import require_admin
from api.auth.models import ROLE_ADMIN, VALID_ROLES, UserRecord
from api.routes.meetings import _resolve_recording_path

router = APIRouter(prefix="/admin", tags=["admin"])


def _remaining_admin_count(users: List[UserRecord], excluding_id: str) -> int:
    return sum(1 for u in users if u.role == ROLE_ADMIN and u.id != excluding_id)


@router.get("/users")
async def list_users(
    request: Request,
    _: UserRecord = Depends(require_admin),
) -> Dict[str, Any]:
    users = request.app.state.user_store.list_users()
    return {"users": [u.to_public_dict() for u in users]}


@router.get("/meetings")
async def list_all_meetings(
    request: Request,
    _: UserRecord = Depends(require_admin),
    limit: int = 200,
    offset: int = 0,
) -> Dict[str, Any]:
    settings = request.app.state.settings
    meetings = request.app.state.manager.store.list_all_meetings(
        limit=limit, offset=offset
    )
    users_by_id = {u.id: u for u in request.app.state.user_store.list_users()}

    out = []
    for m in meetings:
        data = m.to_dict()
        path = _resolve_recording_path(
            m.id,
            m.audio_path,
            audio_dir=settings.audio_dir,
            upload_dir=settings.upload_dir,
        )
        data["has_recording"] = path is not None
        owner = users_by_id.get(m.user_id) if m.user_id else None
        data["owner"] = owner.to_public_dict() if owner else None
        out.append(data)
    return {"meetings": out}


@router.patch("/users/{user_id}/role")
async def set_user_role(
    user_id: str,
    request: Request,
    admin: UserRecord = Depends(require_admin),
) -> Dict[str, Any]:
    try:
        raw = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    role = str((raw or {}).get("role") or "")
    if role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="invalid role")

    user_store = request.app.state.user_store
    target = user_store.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="user not found")

    if target.id == admin.id and role != ROLE_ADMIN:
        users = user_store.list_users()
        if _remaining_admin_count(users, excluding_id=admin.id) == 0:
            raise HTTPException(
                status_code=400, detail="cannot remove the only remaining admin"
            )

    target.role = role
    user_store.save_user(target)
    return {"user": target.to_public_dict()}


@router.patch("/users/{user_id}/active")
async def set_user_active(
    user_id: str,
    request: Request,
    admin: UserRecord = Depends(require_admin),
) -> Dict[str, Any]:
    try:
        raw = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("is_active"), bool):
        raise HTTPException(status_code=400, detail="is_active (bool) required")
    is_active = raw["is_active"]

    user_store = request.app.state.user_store
    target = user_store.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="user not found")

    if target.id == admin.id and not is_active:
        raise HTTPException(status_code=400, detail="cannot deactivate your own account")

    target.is_active = is_active
    user_store.save_user(target)
    return {"user": target.to_public_dict()}
