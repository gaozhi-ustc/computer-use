"""Users management API router (admin only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from server import db
from server.auth import hash_password
from server.auth_router import get_current_user
from server.models import UserCreate, UserInfo, UserListResponse, UserUpdate

router = APIRouter(prefix="/api/users", tags=["users"])


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


@router.get("/", response_model=UserListResponse)
def list_users(
    role: str | None = None,
    limit: int = 100,
    offset: int = 0,
    _admin: dict = Depends(require_admin),
):
    users = db.list_users(role=role, limit=limit, offset=offset)
    all_users = db.list_users(role=role, limit=10000, offset=0)
    return UserListResponse(
        total=len(all_users),
        users=[UserInfo(**{k: v for k, v in u.items()
                          if k in UserInfo.model_fields}) for u in users],
    )


@router.post("/", response_model=UserInfo, status_code=201)
def create_user(req: UserCreate, _admin: dict = Depends(require_admin)):
    pw_hash = hash_password(req.password) if req.password else None
    try:
        uid = db.insert_user(
            username=req.username,
            password_hash=pw_hash,
            display_name=req.display_name,
            role=req.role,
            employee_id=req.employee_id,
            department=req.department,
            department_id=req.department_id,
        )
    except Exception:
        raise HTTPException(status_code=409, detail="Username already exists")
    user = db.get_user_by_id(uid)
    return UserInfo(**{k: v for k, v in user.items() if k in UserInfo.model_fields})


@router.put("/{user_id}", response_model=UserInfo)
def update_user(user_id: int, req: UserUpdate, _admin: dict = Depends(require_admin)):
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    fields = {}
    for field_name in ("display_name", "role", "employee_id",
                       "department", "department_id", "is_active"):
        val = getattr(req, field_name, None)
        if val is not None:
            fields[field_name] = val
    if req.password:
        fields["password_hash"] = hash_password(req.password)
    if fields:
        db.update_user(user_id, **fields)
    updated = db.get_user_by_id(user_id)
    return UserInfo(**{k: v for k, v in updated.items() if k in UserInfo.model_fields})


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, _admin: dict = Depends(require_admin)):
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete_user(user_id)
