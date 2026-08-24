from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth.deps import get_current_user
from api.tuning import _sanitize, make_tuning, public_tuning_payload

router = APIRouter(
    tags=["tuning"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/tuning")
async def get_tuning(request: Request) -> Dict[str, Any]:
    return public_tuning_payload(request.app.state.tuning)


@router.put("/tuning")
async def put_tuning(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc

    values = body.get("values") if isinstance(body, dict) else None
    if not isinstance(values, dict):
        values = body if isinstance(body, dict) else {}

    cleaned = _sanitize(values)
    tuning = request.app.state.tuning
    tuning.update(cleaned)
    request.app.state.manager.apply_tuning(tuning)
    return public_tuning_payload(tuning)


@router.post("/tuning/reset")
async def reset_tuning(request: Request) -> Dict[str, Any]:
    fresh = make_tuning()
    request.app.state.tuning.clear()
    request.app.state.tuning.update(fresh)
    request.app.state.manager.apply_tuning(fresh)
    return public_tuning_payload(fresh)
