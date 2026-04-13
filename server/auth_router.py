"""Auth API router: login, refresh, me."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, status

from server import db
from server.auth import (
    AuthError, create_access_token, create_refresh_token,
    decode_token, verify_password,
)
from server.models import (
    LoginRequest, RefreshRequest, TokenResponse, UserInfo,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    """Extract and validate JWT from Authorization header. Returns user dict."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Not an access token")
    user = db.get_user_by_id(payload["sub"])
    if not user or not user.get("is_active"):
        raise HTTPException(status_code=401, detail="User not found or disabled")
    return user


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest):
    user = db.get_user_by_username(req.username)
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.update_user(user["id"], last_login=now)
    return TokenResponse(
        access_token=create_access_token(user["id"], user["username"], user["role"]),
        refresh_token=create_refresh_token(user["id"]),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(req: RefreshRequest):
    try:
        payload = decode_token(req.refresh_token)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Not a refresh token")
    user = db.get_user_by_id(payload["sub"])
    if not user or not user.get("is_active"):
        raise HTTPException(status_code=401, detail="User not found or disabled")
    return TokenResponse(
        access_token=create_access_token(user["id"], user["username"], user["role"]),
        refresh_token=create_refresh_token(user["id"]),
    )


@router.get("/me", response_model=UserInfo)
def me(current_user: dict = Depends(get_current_user)):
    return UserInfo(
        id=current_user["id"],
        username=current_user["username"],
        display_name=current_user["display_name"],
        avatar_url=current_user.get("avatar_url", ""),
        role=current_user["role"],
        employee_id=current_user.get("employee_id"),
        department=current_user.get("department", ""),
        department_id=current_user.get("department_id", ""),
        is_dept_manager=bool(current_user.get("is_dept_manager", False)),
    )
