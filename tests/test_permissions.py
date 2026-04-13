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
