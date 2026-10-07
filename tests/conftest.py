"""Shared test fixtures. pytest loads this file automatically."""

from datetime import date

import pytest

from agent import tools
from data.seed import build

TEST_TODAY = date(2026, 1, 15)


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """A fresh fake database in a temp folder, with the tools pointed at it and the date pinned."""
    path = build(tmp_path / "test.db", today=TEST_TODAY)
    monkeypatch.setattr(tools, "DB_PATH", path)
    monkeypatch.setattr(tools, "today", lambda: TEST_TODAY)
    return path
