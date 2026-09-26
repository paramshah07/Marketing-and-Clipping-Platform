"""Tests run in the api container against the compose postgres, in a throwaway database
(clipper_test) rebuilt at the start of every session:

    docker compose run --rm api pytest
"""

import os

ADMIN_URL = os.environ["DATABASE_URL"]
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + "/clipper_test"
os.environ["DATABASE_URL"] = TEST_URL  # before any app import: settings, engine and queue all use the test db

from pathlib import Path  # noqa: E402

import psycopg  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from procrastinate.schema import SchemaManager  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

ALEMBIC = Config(str(Path(__file__).parents[1] / "alembic.ini"))


def libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def db():
    """Fresh clipper_test with the Alembic migration and Procrastinate's schema; yields a sync engine."""
    with psycopg.connect(libpq(ADMIN_URL), autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS clipper_test WITH (FORCE)")
        conn.execute("CREATE DATABASE clipper_test")
    command.upgrade(ALEMBIC, "head")
    with psycopg.connect(libpq(TEST_URL), autocommit=True) as conn:
        conn.execute(SchemaManager.get_schema())
    engine = create_engine(TEST_URL)
    yield engine
    engine.dispose()
