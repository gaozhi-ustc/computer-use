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


# ---------------------------------------------------------------------------
# SOP models
# ---------------------------------------------------------------------------


class SopCreate(BaseModel):
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    assigned_reviewer: Optional[str] = None
    source_session_id: Optional[str] = None
    source_employee_id: Optional[str] = None


class SopUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    assigned_reviewer: Optional[str] = None


class SopStatusUpdate(BaseModel):
    status: str  # in_review, published, draft (reject)


class SopInfo(BaseModel):
    id: int
    title: str
    description: str = ""
    status: str
    created_by: str
    assigned_reviewer: Optional[str] = None
    source_session_id: Optional[str] = None
    source_employee_id: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    published_at: Optional[str] = None


class StepInfo(BaseModel):
    id: int
    sop_id: int
    step_order: int
    title: str
    description: str = ""
    application: str = ""
    action_type: str = ""
    action_detail: dict | list = Field(default_factory=dict)
    screenshot_ref: str = ""
    source_frame_ids: list[int] = Field(default_factory=list)
    confidence: float = 0.0
    created_at: str
    updated_at: str


class SopDetail(SopInfo):
    steps: list[StepInfo] = Field(default_factory=list)


class SopListResponse(BaseModel):
    total: int
    count: int
    sops: list[SopInfo]


class StepCreate(BaseModel):
    step_order: int
    title: str
    description: str = ""
    application: str = ""
    action_type: str = ""
    action_detail: dict | list = Field(default_factory=dict)
    screenshot_ref: str = ""
    source_frame_ids: list[int] = Field(default_factory=list)
    confidence: float = 0.0


class StepUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    step_order: Optional[int] = None
    application: Optional[str] = None
    action_type: Optional[str] = None
    action_detail: Optional[dict | list] = None
    screenshot_ref: Optional[str] = None
    source_frame_ids: Optional[list[int]] = None
    confidence: Optional[float] = None


class StepReorder(BaseModel):
    step_ids: list[int]
