"""Tests for api_keys.txt parser."""

from __future__ import annotations

import pytest


def test_load_keys_happy_path(tmp_path):
    from server.api_keys import load_api_keys
    f = tmp_path / "keys.txt"
    f.write_text("sk-sp-aaa\nsk-sp-bbb\nsk-sp-ccc\n", encoding="utf-8")
    assert load_api_keys(f) == ["sk-sp-aaa", "sk-sp-bbb", "sk-sp-ccc"]


def test_load_keys_strips_whitespace(tmp_path):
    from server.api_keys import load_api_keys
    f = tmp_path / "keys.txt"
    f.write_text("  sk-sp-aaa  \n\t sk-sp-bbb\n", encoding="utf-8")
    assert load_api_keys(f) == ["sk-sp-aaa", "sk-sp-bbb"]


def test_load_keys_skips_blank_lines_and_comments(tmp_path):
    from server.api_keys import load_api_keys
    f = tmp_path / "keys.txt"
    f.write_text(
        "# header comment\n"
        "\n"
        "sk-sp-aaa\n"
        "   \n"
        "# another comment\n"
        "sk-sp-bbb  # inline is NOT stripped\n",
        encoding="utf-8",
    )
    assert load_api_keys(f) == ["sk-sp-aaa", "sk-sp-bbb  # inline is NOT stripped"]


def test_load_keys_missing_file_returns_empty(tmp_path):
    from server.api_keys import load_api_keys
    assert load_api_keys(tmp_path / "nope.txt") == []


def test_load_keys_empty_file_returns_empty(tmp_path):
    from server.api_keys import load_api_keys
    f = tmp_path / "keys.txt"
    f.write_text("", encoding="utf-8")
    assert load_api_keys(f) == []


def test_load_keys_all_comments_returns_empty(tmp_path):
    from server.api_keys import load_api_keys
    f = tmp_path / "keys.txt"
    f.write_text("# all comments\n# nothing else\n", encoding="utf-8")
    assert load_api_keys(f) == []


def test_load_keys_default_path(tmp_path, monkeypatch):
    """When called without args, reads ./api_keys.txt from CWD."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "api_keys.txt").write_text("sk-sp-default\n", encoding="utf-8")
    from server.api_keys import load_api_keys
    assert load_api_keys() == ["sk-sp-default"]
