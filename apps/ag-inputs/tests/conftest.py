"""Test setup: a throwaway SQLite database per test run, schema built fresh per test."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_DB = Path(tempfile.mkdtemp()) / "test.db"
os.environ["AG_DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["AG_AUTH_MODE"] = "dev"  # identity from X-Dev-User headers; never used in production
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ag_inputs  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def db_schema():
    # Tests build the schema directly from the models; production uses Alembic.
    ag_inputs.Base.metadata.drop_all(ag_inputs.engine)
    ag_inputs.Base.metadata.create_all(ag_inputs.engine)
    yield
    ag_inputs.Base.metadata.drop_all(ag_inputs.engine)


def as_user(subject: str, name: str | None = None, email: str | None = None) -> dict[str, str]:
    h = {"X-Dev-User": subject, "X-Dev-Name": name or subject.title()}
    if email:
        h["X-Dev-Email"] = email
    return h


@pytest.fixture()
def client(db_schema):
    """Signed in as 'owner' (the farm creator in most tests)."""
    return TestClient(ag_inputs.app, headers=as_user("owner", email="owner@example.com"))


@pytest.fixture()
def farm_id(client):
    return client.post("/api/farms", json={"name": "Test Farm"}).json()["id"]
