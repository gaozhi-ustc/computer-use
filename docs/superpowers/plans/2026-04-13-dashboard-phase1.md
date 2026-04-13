# Dashboard Phase 1: Backend Foundation — Auth + DB + Users API

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the authentication and user management backend that all subsequent dashboard features depend on.

**Architecture:** Extend the existing FastAPI server (`server/app.py`) with JWT auth, bcrypt password hashing, role-based permission middleware, and a users CRUD API. Add `users` and `sops`/`sop_steps` tables to the existing SQLite schema. The existing `/frames` and `/health` endpoints continue to work unchanged; new auth-protected endpoints live under `/api/auth/` and `/api/users/`.

**Tech Stack:** FastAPI (existing), PyJWT, passlib[bcrypt], existing sqlite3 via `server/db.py`

**Phases roadmap (this plan covers Phase 1 only):**

| Phase | Deliverable | Depends on |
|-------|-------------|-----------|
| **1. Backend Foundation** (this plan) | Auth + Users API + DB schema + permission middleware | — |
| 2. Frontend Shell | Vue 3 scaffold + Login page + Sidebar layout + Router guards | Phase 1 |
| 3. Sessions + Recording Playback | Sessions API + Frames auth-filtering + Recording page | Phase 1-2 |
| 4. SOP Backend + Editor | SOP CRUD + Steps API + Auto-extract + Editor page + Export | Phase 1-3 |
| 5. Analytics + Audit + Dashboard | Stats API + Charts + Audit query + Overview page | Phase 1-3 |
| 6. DingTalk Integration | SSO login + Org tree sync | Phase 1-2 |

---

## File Structure

### New files

```
server/
├── auth.py              # JWT creation/verification, password hashing, get_current_user dependency
├── models.py            # Pydantic schemas for User, auth requests/responses
├── users.py             # Users CRUD API router
├── permissions.py       # Role-based data filter helper
tests/
├── test_auth.py         # JWT + password hashing tests
├── test_users_api.py    # Users CRUD endpoint tests
├── test_permissions.py  # Role-based filtering tests
```

### Modified files

```
server/db.py             # Add users + sops + sop_steps tables to SCHEMA, add user CRUD functions
server/app.py            # Mount auth + users routers, keep existing /frames + /health unchanged
pyproject.toml           # Add PyJWT, passlib[bcrypt] to server extras
```

---

### Task 1: Add dependencies

**Files:**
- Modify: `pyproject.toml:28-31`

- [ ] **Step 1: Add auth dependencies to server extras**

```toml
server = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "PyJWT>=2.8",
    "passlib[bcrypt]>=1.7",
]
```

- [ ] **Step 2: Install**

Run: `pip install -e ".[server]"`
Expected: Successfully installed PyJWT passlib bcrypt

- [ ] **Step 3: Verify imports**

Run: `python -c "import jwt; from passlib.hash import bcrypt; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "deps: add PyJWT and passlib[bcrypt] for dashboard auth"
```

---

### Task 2: Extend DB schema with users table

**Files:**
- Modify: `server/db.py`
- Test: `tests/test_server_db.py` (extend)

- [ ] **Step 1: Write failing test for user insert/query**

Add to `tests/test_server_db.py`:

```python
def test_insert_user_and_query(fresh_db):
    from server.db import insert_user, get_user_by_username
    user_id = insert_user(
        username="testadmin",
        password_hash="$2b$12$fakehash",
        display_name="Test Admin",
        role="admin",
        employee_id="E001",
    )
    assert user_id == 1
    user = get_user_by_username("testadmin")
    assert user is not None
    assert user["username"] == "testadmin"
    assert user["role"] == "admin"
    assert user["employee_id"] == "E001"
    assert user["is_active"] == 1


def test_insert_duplicate_username_raises(fresh_db):
    from server.db import insert_user
    insert_user(username="dup", password_hash="x", display_name="A", role="employee")
    import sqlite3
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        insert_user(username="dup", password_hash="y", display_name="B", role="employee")


def test_get_user_by_id(fresh_db):
    from server.db import insert_user, get_user_by_id
    uid = insert_user(username="byid", password_hash="x", display_name="ByID", role="employee")
    user = get_user_by_id(uid)
    assert user["username"] == "byid"


def test_list_users(fresh_db):
    from server.db import insert_user, list_users
    insert_user(username="a", password_hash="x", display_name="A", role="admin")
    insert_user(username="b", password_hash="x", display_name="B", role="employee")
    insert_user(username="c", password_hash="x", display_name="C", role="manager")
    users = list_users()
    assert len(users) == 3


def test_update_user(fresh_db):
    from server.db import insert_user, update_user, get_user_by_id
    uid = insert_user(username="upd", password_hash="x", display_name="Old", role="employee")
    update_user(uid, display_name="New", role="manager")
    user = get_user_by_id(uid)
    assert user["display_name"] == "New"
    assert user["role"] == "manager"


def test_delete_user(fresh_db):
    from server.db import insert_user, delete_user, get_user_by_id
    uid = insert_user(username="del", password_hash="x", display_name="Del", role="employee")
    delete_user(uid)
    assert get_user_by_id(uid) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_server_db.py::test_insert_user_and_query -v`
Expected: FAIL — `ImportError: cannot import name 'insert_user' from 'server.db'`

- [ ] **Step 3: Add users table to SCHEMA and implement CRUD functions in db.py**

Add to `server/db.py` SCHEMA string (after existing frames schema):

```python
# Append to SCHEMA:
USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dingtalk_userid TEXT UNIQUE,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT,
    display_name TEXT NOT NULL,
    avatar_url TEXT DEFAULT '',
    role TEXT NOT NULL CHECK(role IN ('admin', 'manager', 'employee')),
    employee_id TEXT,
    department TEXT DEFAULT '',
    department_id TEXT DEFAULT '',
    is_dept_manager BOOLEAN DEFAULT 0,
    is_active BOOLEAN DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS sops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('draft', 'in_review', 'published')),
    created_by TEXT NOT NULL,
    assigned_reviewer TEXT,
    source_session_id TEXT,
    source_employee_id TEXT,
    tags TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS sop_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sop_id INTEGER NOT NULL REFERENCES sops(id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    application TEXT DEFAULT '',
    action_type TEXT DEFAULT '',
    action_detail TEXT DEFAULT '{}',
    screenshot_ref TEXT DEFAULT '',
    source_frame_ids TEXT DEFAULT '[]',
    confidence REAL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sops_status ON sops(status);
CREATE INDEX IF NOT EXISTS idx_sop_steps_sop ON sop_steps(sop_id, step_order);
"""
```

Update `init_db()`:

```python
def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.executescript(USERS_SCHEMA)
```

Add user CRUD functions:

```python
def insert_user(
    username: str,
    password_hash: str | None = None,
    display_name: str = "",
    role: str = "employee",
    employee_id: str | None = None,
    dingtalk_userid: str | None = None,
    department: str = "",
    department_id: str = "",
    is_dept_manager: bool = False,
) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO users (dingtalk_userid, username, password_hash,
               display_name, role, employee_id, department, department_id,
               is_dept_manager, is_active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (dingtalk_userid, username, password_hash, display_name, role,
             employee_id, department, department_id, is_dept_manager, now),
        )
        return cur.lastrowid


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_dingtalk(dingtalk_userid: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE dingtalk_userid = ?", (dingtalk_userid,)
        ).fetchone()
    return dict(row) if row else None


def list_users(
    role: str | None = None,
    department_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if role:
        clauses.append("role = ?")
        params.append(role)
    if department_id:
        clauses.append("department_id = ?")
        params.append(department_id)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.extend([limit, offset])
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM users {where} ORDER BY id LIMIT ? OFFSET ?", params
        ).fetchall()
    return [dict(r) for r in rows]


def update_user(user_id: int, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [user_id]
    with connect() as conn:
        conn.execute(f"UPDATE users SET {sets} WHERE id = ?", vals)


def delete_user(user_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def get_department_employee_ids(department_id: str) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT employee_id FROM users WHERE department_id = ? AND employee_id IS NOT NULL",
            (department_id,),
        ).fetchall()
    return [r["employee_id"] for r in rows]
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python -m pytest tests/test_server_db.py -v -k "user"`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add server/db.py tests/test_server_db.py
git commit -m "feat: users + sops DB schema and user CRUD functions"
```

---

### Task 3: Auth module — JWT + password hashing

**Files:**
- Create: `server/auth.py`
- Create: `tests/test_auth.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_auth.py`:

```python
"""Tests for JWT and password utilities."""

import time
import pytest


def test_hash_and_verify_password():
    from server.auth import hash_password, verify_password
    hashed = hash_password("secret123")
    assert hashed != "secret123"
    assert verify_password("secret123", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_create_and_decode_access_token():
    from server.auth import create_access_token, decode_token
    token = create_access_token(user_id=42, username="alice", role="admin")
    payload = decode_token(token)
    assert payload["sub"] == 42
    assert payload["username"] == "alice"
    assert payload["role"] == "admin"
    assert payload["type"] == "access"


def test_create_and_decode_refresh_token():
    from server.auth import create_refresh_token, decode_token
    token = create_refresh_token(user_id=42)
    payload = decode_token(token)
    assert payload["sub"] == 42
    assert payload["type"] == "refresh"


def test_expired_token_raises(monkeypatch):
    from server.auth import create_access_token, decode_token, AuthError
    # Create a token that's already expired
    monkeypatch.setattr("server.auth.ACCESS_TOKEN_EXPIRE_MINUTES", -1)
    token = create_access_token(user_id=1, username="x", role="employee")
    with pytest.raises(AuthError, match="expired"):
        decode_token(token)


def test_invalid_token_raises():
    from server.auth import decode_token, AuthError
    with pytest.raises(AuthError):
        decode_token("not.a.valid.jwt")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.auth'`

- [ ] **Step 3: Implement server/auth.py**

```python
"""JWT token management and password hashing."""

from __future__ import annotations

import os
import time
from typing import Any

import jwt
from passlib.hash import bcrypt


class AuthError(Exception):
    """Raised on token validation failure."""
    pass


# Config from env, with sensible defaults
SECRET_KEY = os.environ.get("DASHBOARD_SECRET_KEY", "dev-secret-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


def hash_password(password: str) -> str:
    return bcrypt.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.verify(password, hashed)


def create_access_token(user_id: int, username: str, role: str) -> str:
    expire = time.time() + ACCESS_TOKEN_EXPIRE_MINUTES * 60
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "type": "access",
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    expire = time.time() + REFRESH_TOKEN_EXPIRE_DAYS * 86400
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise AuthError("Token expired")
    except jwt.InvalidTokenError as e:
        raise AuthError(f"Invalid token: {e}")
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python -m pytest tests/test_auth.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add server/auth.py tests/test_auth.py
git commit -m "feat: JWT token + bcrypt password hashing module"
```

---

### Task 4: Pydantic models for auth API

**Files:**
- Create: `server/models.py`

- [ ] **Step 1: Create models file**

```python
"""Pydantic schemas for auth and user management APIs."""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


# --- Auth ---

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


# --- User Management ---

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
    password: Optional[str] = None  # set/reset password


class UserListResponse(BaseModel):
    total: int
    users: list[UserInfo]
```

- [ ] **Step 2: Verify import**

Run: `PYTHONPATH=src python -c "from server.models import LoginRequest, TokenResponse, UserInfo; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add server/models.py
git commit -m "feat: Pydantic models for auth and user management"
```

---

### Task 5: Permission middleware

**Files:**
- Create: `server/permissions.py`
- Create: `tests/test_permissions.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_permissions.py`:

```python
"""Tests for role-based permission helpers."""

import pytest
from server.permissions import filter_employee_ids


def test_admin_sees_all():
    user = {"role": "admin", "employee_id": "E001", "department_id": "D1"}
    result = filter_employee_ids(user, all_ids=["E001", "E002", "E003"])
    assert result is None  # None means "no filter, show all"


def test_employee_sees_only_self():
    user = {"role": "employee", "employee_id": "E002", "department_id": "D1"}
    result = filter_employee_ids(user)
    assert result == ["E002"]


def test_employee_without_id_sees_nothing():
    user = {"role": "employee", "employee_id": None, "department_id": "D1"}
    result = filter_employee_ids(user)
    assert result == []


def test_manager_sees_department(monkeypatch, tmp_path):
    import server.db as db
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    db.init_db()
    db.insert_user(username="m1", display_name="M", role="manager",
                   employee_id="E001", department_id="D1")
    db.insert_user(username="e1", display_name="E1", role="employee",
                   employee_id="E010", department_id="D1")
    db.insert_user(username="e2", display_name="E2", role="employee",
                   employee_id="E020", department_id="D1")
    db.insert_user(username="e3", display_name="E3", role="employee",
                   employee_id="E030", department_id="D2")  # different dept

    user = {"role": "manager", "employee_id": "E001", "department_id": "D1"}
    result = filter_employee_ids(user)
    assert set(result) == {"E001", "E010", "E020"}
    assert "E030" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_permissions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.permissions'`

- [ ] **Step 3: Implement permissions.py**

```python
"""Role-based data access filtering."""

from __future__ import annotations
from typing import Any, Optional

from server import db


def filter_employee_ids(
    current_user: dict[str, Any],
    all_ids: list[str] | None = None,
) -> list[str] | None:
    """Return the list of employee_ids this user is allowed to see.

    Returns None for admin (= no filter, see everything).
    Returns a list for manager/employee (= restrict to these IDs).
    """
    role = current_user.get("role", "employee")

    if role == "admin":
        return None  # no restriction

    if role == "manager":
        dept_id = current_user.get("department_id", "")
        if dept_id:
            return db.get_department_employee_ids(dept_id)
        own = current_user.get("employee_id")
        return [own] if own else []

    # employee
    own = current_user.get("employee_id")
    return [own] if own else []
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python -m pytest tests/test_permissions.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add server/permissions.py tests/test_permissions.py
git commit -m "feat: role-based employee_id filtering middleware"
```

---

### Task 6: Auth API router

**Files:**
- Create: `server/auth_router.py`
- Create: `tests/test_auth_api.py`

- [ ] **Step 1: Write failing tests using FastAPI TestClient**

Create `tests/test_auth_api.py`:

```python
"""Tests for auth API endpoints."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "test-secret")
    from server.app import app
    from server import db
    db.init_db()
    # Seed an admin user with password
    from server.auth import hash_password
    db.insert_user(
        username="admin",
        password_hash=hash_password("admin123"),
        display_name="Admin",
        role="admin",
        employee_id="E000",
    )
    return TestClient(app)


def test_login_success(client):
    resp = client.post("/api/auth/login", json={
        "username": "admin", "password": "admin123"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_login_wrong_password(client):
    resp = client.post("/api/auth/login", json={
        "username": "admin", "password": "wrong"
    })
    assert resp.status_code == 401


def test_login_nonexistent_user(client):
    resp = client.post("/api/auth/login", json={
        "username": "nobody", "password": "x"
    })
    assert resp.status_code == 401


def test_me_with_valid_token(client):
    login = client.post("/api/auth/login", json={
        "username": "admin", "password": "admin123"
    })
    token = login.json()["access_token"]
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "admin"
    assert data["role"] == "admin"


def test_me_without_token(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_refresh_token(client):
    login = client.post("/api/auth/login", json={
        "username": "admin", "password": "admin123"
    })
    refresh = login.json()["refresh_token"]
    resp = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200
    assert "access_token" in resp.json()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_auth_api.py -v`
Expected: FAIL — `ImportError` (auth_router not mounted yet)

- [ ] **Step 3: Implement server/auth_router.py**

```python
"""Auth API router: login, refresh, me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Header, status
from typing import Optional

from server import db
from server.auth import (
    AuthError, create_access_token, create_refresh_token,
    decode_token, hash_password, verify_password,
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
    db.update_user(user["id"], last_login=__import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat(timespec="seconds"))
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
```

- [ ] **Step 4: Mount router in server/app.py**

Add to `server/app.py` after existing imports:

```python
from server.auth_router import router as auth_router

# After app = FastAPI(...):
app.include_router(auth_router)
```

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=src python -m pytest tests/test_auth_api.py -v`
Expected: 6 passed

- [ ] **Step 6: Run full test suite for regression**

Run: `PYTHONPATH=src python -m pytest tests/ -v --no-header -q`
Expected: all existing + new tests pass, 0 failures

- [ ] **Step 7: Commit**

```bash
git add server/auth_router.py server/app.py tests/test_auth_api.py
git commit -m "feat: auth API — login, refresh, me endpoints with JWT"
```

---

### Task 7: Users CRUD API router (admin only)

**Files:**
- Create: `server/users_router.py`
- Create: `tests/test_users_api.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_users_api.py`:

```python
"""Tests for users management API (admin only)."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "test-secret")
    from server.app import app
    from server import db
    from server.auth import hash_password
    db.init_db()
    db.insert_user(username="admin", password_hash=hash_password("admin123"),
                   display_name="Admin", role="admin", employee_id="E000")
    db.insert_user(username="emp", password_hash=hash_password("emp123"),
                   display_name="Employee", role="employee", employee_id="E001")
    return TestClient(app)


def _admin_token(client) -> str:
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    return resp.json()["access_token"]


def _emp_token(client) -> str:
    resp = client.post("/api/auth/login", json={"username": "emp", "password": "emp123"})
    return resp.json()["access_token"]


def test_list_users_as_admin(client):
    token = _admin_token(client)
    resp = client.get("/api/users/", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


def test_list_users_as_employee_forbidden(client):
    token = _emp_token(client)
    resp = client.get("/api/users/", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_create_user_as_admin(client):
    token = _admin_token(client)
    resp = client.post("/api/users/", json={
        "username": "newuser", "display_name": "New User",
        "role": "employee", "password": "pass123"
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 201
    assert resp.json()["username"] == "newuser"


def test_update_user_role(client):
    token = _admin_token(client)
    # Get emp user id
    users = client.get("/api/users/", headers={"Authorization": f"Bearer {token}"}).json()
    emp_id = [u for u in users["users"] if u["username"] == "emp"][0]["id"]
    resp = client.put(f"/api/users/{emp_id}", json={"role": "manager"},
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "manager"


def test_delete_user(client):
    token = _admin_token(client)
    # Create then delete
    create = client.post("/api/users/", json={
        "username": "todelete", "display_name": "Del", "role": "employee"
    }, headers={"Authorization": f"Bearer {token}"})
    uid = create.json()["id"]
    resp = client.delete(f"/api/users/{uid}",
                         headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_users_api.py -v`
Expected: FAIL — import error or 404

- [ ] **Step 3: Implement server/users_router.py**

```python
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
    # Count total (simple approach: query without limit)
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
```

- [ ] **Step 4: Mount router in server/app.py**

```python
from server.users_router import router as users_router
app.include_router(users_router)
```

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=src python -m pytest tests/test_users_api.py -v`
Expected: 5 passed

- [ ] **Step 6: Run full suite**

Run: `PYTHONPATH=src python -m pytest tests/ -q --no-header`
Expected: all pass, 0 failures

- [ ] **Step 7: Commit**

```bash
git add server/users_router.py server/app.py tests/test_users_api.py
git commit -m "feat: users CRUD API with admin-only access control"
```

---

### Task 8: Seed initial admin user on first startup

**Files:**
- Modify: `server/app.py`

- [ ] **Step 1: Add admin seeding to startup**

In `server/app.py`, update the `_startup` function:

```python
@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    # Seed default admin if no users exist yet
    if not db.list_users(limit=1):
        from server.auth import hash_password
        db.insert_user(
            username="admin",
            password_hash=hash_password("admin"),
            display_name="System Admin",
            role="admin",
        )
        import structlog
        structlog.get_logger().info("seeded_default_admin",
                                     username="admin", password="admin",
                                     msg="Change this password immediately!")
```

- [ ] **Step 2: Manual smoke test**

Run: `WORKFLOW_SERVER_DB=./test_seed.db PYTHONPATH=src python -c "from server.app import app; print('startup hooks registered')"`
Expected: no errors

- [ ] **Step 3: Clean up and commit**

```bash
rm -f test_seed.db
git add server/app.py
git commit -m "feat: seed default admin user on first startup"
```

---

## Phase 1 Completion Criteria

After all 8 tasks, the system should support:

1. `POST /api/auth/login` with username/password -> JWT tokens
2. `GET /api/auth/me` with Bearer token -> user info
3. `POST /api/auth/refresh` -> new access token
4. `GET /api/users/` (admin only) -> paginated user list
5. `POST /api/users/` (admin only) -> create user
6. `PUT /api/users/:id` (admin only) -> update user
7. `DELETE /api/users/:id` (admin only) -> delete user
8. Non-admin users get 403 on user management endpoints
9. Default `admin`/`admin` account seeded on first launch
10. Existing `/frames`, `/frames/batch`, `/health` endpoints unchanged
11. All new code covered by tests; full suite green

**Verification command:**

```bash
PYTHONPATH=src python -m pytest tests/ -v --no-header
# Expected: ~155+ passed (133 existing + ~22 new), 7 skipped
```
