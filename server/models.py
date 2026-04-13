"""Pydantic schemas for auth and user management APIs."""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserInfo(BaseModel):
    id: int
    username: str
    display_name: str
    avatar_url: str = ""
    role: str
    employee_id: Optional[str] = None
    department: str = ""
    department_id: str = ""
    is_dept_manager: bool = False


class UserCreate(BaseModel):
    username: str
    password: Optional[str] = None
    display_name: str
    role: str = "employee"
    employee_id: Optional[str] = None
    department: str = ""
    department_id: str = ""


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    employee_id: Optional[str] = None
    department: Optional[str] = None
    department_id: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


class UserListResponse(BaseModel):
    total: int
    users: list[UserInfo]
