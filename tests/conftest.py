"""Shared test fixtures. pytest loads this file automatically."""

from datetime import date

import pytest

from agent import tools
from data.seed import build


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """A fresh fake database in a temp folder, with the tools pointed at it."""
    path = build(tmp_path / "test.db", today=date(2026, 1, 15))
    monkeypatch.setattr(tools, "DB_PATH", path)
    return path
