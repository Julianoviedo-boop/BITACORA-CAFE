"""Fixtures de base de datos para las pruebas.

Las pruebas de base de datos corren contra PostgreSQL real (JSONB, ON CONFLICT y
las FKs compuestas no se pueden probar en SQLite). Se usa una base aparte,
`<DATABASE_URL>_test` (o TEST_DATABASE_URL), creada desde cero con las
migraciones de Alembic, así que también se prueba la migración.

Si PostgreSQL no está disponible, esas pruebas se saltan con un aviso; con
REQUIRE_DB_TESTS=1 (recomendado en CI) fallan en lugar de saltarse.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _test_url() -> URL:
    if explicit := os.environ.get("TEST_DATABASE_URL"):
        return make_url(explicit)
    url = make_url(get_settings().database_url)
    return url.set(database=f"{url.database}_test")


def _recreate_database(url: URL) -> None:
    admin = create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 3},
    )
    try:
        with admin.connect() as conn:
            name = url.database
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    url = _test_url()
    try:
        _recreate_database(url)
    except Exception as exc:  # noqa: BLE001
        msg = f"PostgreSQL no disponible para pruebas ({url.render_as_string()}): {exc}"
        if os.environ.get("REQUIRE_DB_TESTS") == "1":
            pytest.fail(msg)
        pytest.skip(msg)

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """Sesión dentro de una transacción que se deshace al final de cada prueba."""
    connection = db_engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()
