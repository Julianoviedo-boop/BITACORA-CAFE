"""Alertas abiertas y resueltas

- alerts.status admite "resuelta": la alerta se cerró sola (llegó el evento que
  faltaba, el valor volvió al rango...).
- alerts.resolved_at: cuándo se cerró.
- Índice único parcial (organization_id, lot_id, rule_code) sobre las alertas
  abiertas (pendiente o enviada): la misma alerta no se repite mientras siga
  abierta, aunque dos workers revisen el mismo lote a la vez.

Revision ID: d5a2c7e1f8b9
Revises: c3e8f1a90b47
Create Date: 2026-11-13 18:00:00+00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a2c7e1f8b9"
down_revision: str | None = "c3e8f1a90b47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint(op.f("ck_alerts_status"), "alerts", type_="check")
    op.create_check_constraint(
        op.f("ck_alerts_status"), "alerts",
        "status IN ('pendiente', 'enviada', 'resuelta', 'descartada')",
    )
    op.create_index(
        "uq_alerts_open_rule", "alerts", ["organization_id", "lot_id", "rule_code"],
        unique=True, postgresql_where=sa.text("status IN ('pendiente', 'enviada')"),
    )


def downgrade() -> None:
    op.drop_index("uq_alerts_open_rule", table_name="alerts",
                  postgresql_where=sa.text("status IN ('pendiente', 'enviada')"))
    # Las resueltas pasan a descartadas: el estado anterior no tenía "resuelta".
    op.execute("UPDATE alerts SET status = 'descartada' WHERE status = 'resuelta'")
    op.drop_constraint(op.f("ck_alerts_status"), "alerts", type_="check")
    op.create_check_constraint(
        op.f("ck_alerts_status"), "alerts",
        "status IN ('pendiente', 'enviada', 'descartada')",
    )
    op.drop_column("alerts", "resolved_at")
