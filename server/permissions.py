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
        return None

    if role == "manager":
        dept_id = current_user.get("department_id", "")
        if dept_id:
            return db.get_department_employee_ids(dept_id)
        own = current_user.get("employee_id")
        return [own] if own else []

    # employee
    own = current_user.get("employee_id")
    return [own] if own else []
