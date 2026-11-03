"""Alembic migrations build exactly the schema the models describe."""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

import ag_inputs

ROOT = Path(__file__).resolve().parents[1]


def upgrade(url: str) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["url"] = url
    cfg.attributes["skip_logging"] = True
    command.upgrade(cfg, "head")


def test_migrations_match_models(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade(url)
    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), ag_inputs.Base.metadata)
    assert diff == [], f"Models and migrations disagree: {diff}"


def test_import_does_not_create_tables(tmp_path):
    # The module must not touch the schema at import time (no create_all in production code).
    engine = create_engine(f"sqlite:///{tmp_path / 'untouched.db'}")
    assert inspect(engine).get_table_names() == []
    assert "create_all(" not in (ROOT / "ag_inputs.py").read_text()
