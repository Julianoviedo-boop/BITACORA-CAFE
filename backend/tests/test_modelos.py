"""Reglas del esquema que se verifican sin base de datos."""

import pytest
from sqlalchemy import ForeignKeyConstraint

from app.db.base import Base
from app.db.models import BUSINESS_MODELS

TABLES = [m.__table__ for m in BUSINESS_MODELS]


def test_todas_las_tablas_de_negocio_estan_listadas():
    assert set(Base.metadata.tables) - {"organizations"} == {t.name for t in TABLES}


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_organization_id_obligatorio_e_indexado(table):
    col = table.c.organization_id
    assert not col.nullable
    assert any(ix.columns.keys() == ["organization_id"] for ix in table.indexes)


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_uuid_y_timestamps(table):
    assert table.c.id.primary_key and table.c.id.type.python_type.__name__ == "UUID"
    assert table.c.created_at.type.timezone and table.c.updated_at.type.timezone


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_referencias_entre_negocio_incluyen_organization_id(table):
    """Toda FK hacia otra tabla de negocio debe ser compuesta con organization_id."""
    business = {t.name for t in TABLES}
    for fk in table.constraints:
        if not isinstance(fk, ForeignKeyConstraint):
            continue
        target = fk.elements[0].column.table.name
        if target in business:
            assert "organization_id" in fk.column_keys, f"{table.name} -> {target}"
