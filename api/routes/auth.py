from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from api.auth.deps import get_current_user
from api.auth.models import UserRecord
from api.auth.security import (
    create_access_token,
    hash_password,
    normalize_email,
    validate_email,
    validate_password,
    verify_password,
)
from api.config import Settings

router = APIRouter(prefix="/auth", tags=["auth"])

_SUPPORTED_OAUTH_PROVIDERS = frozenset({"google", "github", "microsoft"})


def _set_auth_cookie(response: Response, token: str, settings: Settings) -> None:
    max_age = settings.jwt_expire_minutes * 60
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=max_age,
        path="/",
    )


def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key="access_token", path="/")


@router.post("/register", status_code=201)
async def register(request: Request) -> Dict[str, Any]:
    try:
        raw = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="expected object")

    email = normalize_email(str(raw.get("email") or ""))
    password = str(raw.get("password") or "")
    display_name = str(raw.get("display_name") or "").strip()

    if not validate_email(email):
        raise HTTPException(status_code=400, detail="invalid email")
    password_error = validate_password(password)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)

    user_store = request.app.state.user_store
    if user_store.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="email already registered")

    user = UserRecord.create(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
    )
    user_store.save_user(user)

    settings: Settings = request.app.state.settings
    token = create_access_token(
        user_id=user.id,
        email=user.email,
        secret=settings.jwt_secret,
        expire_minutes=settings.jwt_expire_minutes,
    )
    body = {"user": user.to_public_dict(), "access_token": token}
    response = JSONResponse(content=body, status_code=201)
    _set_auth_cookie(response, token, settings)
    return response  # type: ignore[return-value]


@router.post("/login")
async def login(request: Request) -> Dict[str, Any]:
    try:
        raw = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="expected object")

    email = normalize_email(str(raw.get("email") or ""))
    password = str(raw.get("password") or "")
    if not email or not password:
        raise HTTPException(status_code=400, detail="email and password required")

    user = request.app.state.user_store.get_user_by_email(email)
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="account disabled")

    settings: Settings = request.app.state.settings
    token = create_access_token(
        user_id=user.id,
        email=user.email,
        secret=settings.jwt_secret,
        expire_minutes=settings.jwt_expire_minutes,
    )
    body = {"user": user.to_public_dict(), "access_token": token}
    response = JSONResponse(content=body)
    _set_auth_cookie(response, token, settings)
    return response  # type: ignore[return-value]


@router.post("/logout")
async def logout() -> Response:
    response = JSONResponse(content={"ok": True})
    _clear_auth_cookie(response)
    return response


@router.get("/me")
async def me(user: UserRecord = Depends(get_current_user)) -> Dict[str, Any]:
    return {"user": user.to_public_dict()}


# --- OAuth stubs (future implementation) ---


@router.get("/oauth/{provider}")
async def oauth_start(provider: str) -> Dict[str, Any]:
    if provider not in _SUPPORTED_OAUTH_PROVIDERS:
        raise HTTPException(status_code=400, detail="unsupported oauth provider")
    raise HTTPException(
        status_code=501,
        detail=f"OAuth via {provider} is not implemented yet",
    )


@router.get("/oauth/{provider}/callback")
async def oauth_callback(provider: str) -> Dict[str, Any]:
    if provider not in _SUPPORTED_OAUTH_PROVIDERS:
        raise HTTPException(status_code=400, detail="unsupported oauth provider")
    raise HTTPException(
        status_code=501,
        detail=f"OAuth callback for {provider} is not implemented yet",
    )


# --- SSO stubs (future implementation) ---


@router.get("/sso/login")
async def sso_login() -> Dict[str, Any]:
    raise HTTPException(status_code=501, detail="SSO login is not implemented yet")


@router.post("/sso/callback")
async def sso_callback() -> Dict[str, Any]:
    raise HTTPException(status_code=501, detail="SSO callback is not implemented yet")
