"""Alembic environment: URL from AG_DATABASE_URL, metadata from the ag_inputs models."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

import ag_inputs

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging"):
    fileConfig(config.config_file_name)

target_metadata = ag_inputs.Base.metadata


def _url() -> str:
    url = config.attributes.get("url") or os.getenv("AG_DATABASE_URL")
    if not url:
        raise RuntimeError("Set AG_DATABASE_URL (e.g. postgresql+psycopg://user:pass@host/db).")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=_url().startswith("sqlite"))
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), future=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
