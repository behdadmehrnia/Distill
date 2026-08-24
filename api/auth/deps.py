from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, Security, WebSocket
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer

from api.config import Settings
from api.meeting.models import MeetingRecord

from .models import UserRecord
from .security import decode_access_token

# Declared as Security schemes so /docs shows Authorize (Bearer + cookie).
_bearer_scheme = HTTPBearer(
    auto_error=False,
    description="JWT from POST /auth/login or /auth/register (Authorization: Bearer …)",
)
_cookie_scheme = APIKeyCookie(
    name="access_token",
    auto_error=False,
    description="HttpOnly JWT cookie set by login/register",
)


def _extract_token(request: Request) -> Optional[str]:
    cookie = request.cookies.get("access_token")
    if cookie:
        return cookie
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return None


def _extract_ws_token(websocket: WebSocket) -> Optional[str]:
    cookie = websocket.cookies.get("access_token")
    if cookie:
        return cookie
    token = websocket.query_params.get("token")
    if token:
        return token.strip()
    return None


def _user_from_token(token: str, *, settings: Settings, user_store) -> Optional[UserRecord]:
    payload = decode_access_token(token, settings.jwt_secret)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    user = user_store.get_user_by_id(str(user_id))
    if not user or not user.is_active:
        return None
    return user


def _credentials_from_security(
    bearer: object,
    cookie_token: object,
) -> Optional[str]:
    """Read injected Security values; ignore unbound Security() defaults."""
    if isinstance(bearer, HTTPAuthorizationCredentials) and bearer.credentials:
        return bearer.credentials.strip() or None
    if isinstance(cookie_token, str) and cookie_token.strip():
        return cookie_token.strip()
    return None


async def get_current_user(
    request: Request,
    bearer: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
    cookie_token: Optional[str] = Security(_cookie_scheme),
) -> UserRecord:
    token = _credentials_from_security(bearer, cookie_token) or _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    user = _user_from_token(
        token,
        settings=request.app.state.settings,
        user_store=request.app.state.user_store,
    )
    if not user:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return user


async def get_optional_user(
    request: Request,
    bearer: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
    cookie_token: Optional[str] = Security(_cookie_scheme),
) -> Optional[UserRecord]:
    token = _credentials_from_security(bearer, cookie_token) or _extract_token(request)
    if not token:
        return None
    return _user_from_token(
        token,
        settings=request.app.state.settings,
        user_store=request.app.state.user_store,
    )


async def require_admin(
    user: UserRecord = Depends(get_current_user),
) -> UserRecord:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin access required")
    return user


async def require_meeting(
    meeting_id: str,
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> MeetingRecord:
    meeting = request.app.state.manager.store.get_meeting(meeting_id)
    if not meeting or meeting.user_id != user.id:
        raise HTTPException(status_code=404, detail="meeting not found")
    return meeting


async def authenticate_websocket(websocket: WebSocket) -> Optional[UserRecord]:
    token = _extract_ws_token(websocket)
    if not token:
        return None
    return _user_from_token(
        token,
        settings=websocket.app.state.settings,
        user_store=websocket.app.state.user_store,
    )
