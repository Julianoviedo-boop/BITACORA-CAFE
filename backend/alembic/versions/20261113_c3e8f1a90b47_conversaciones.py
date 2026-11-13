"""Conversaciones con el productor y eventos sin lote

- conversations: estado de la conversación de cada productor (una fila por
  productor), guardado en la base para que sobreviva a reinicios del worker.
- lot_events.lot_id admite null: hay tipos de evento que no son de un lote
  (requiere_lote: false en config/eventos.yaml, p. ej. una venta). La FK
  compuesta (organization_id, lot_id) se sigue verificando cuando hay lote.

Revision ID: c3e8f1a90b47
Revises: b7c41e9d2f03
Create Date: 2026-11-13 12:00:00+00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3e8f1a90b47"
down_revision: str | None = "b7c41e9d2f03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("producer_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=20), server_default="libre", nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column("drafts", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("event_ids", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("question", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("correction_rounds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_prompt", sa.Text(), nullable=True),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("advisor_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsent_reply", sa.Text(), nullable=True),
        sa.Column("unsent_reply_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "state IN ('libre', 'preguntando', 'ofreciendo_lote', 'confirmando')",
            name=op.f("ck_conversations_state"),
        ),
        sa.CheckConstraint("correction_rounds >= 0",
                           name=op.f("ck_conversations_correction_rounds")),
        sa.ForeignKeyConstraint(
            ["organization_id", "producer_id"], ["producers.organization_id", "producers.id"],
            name=op.f("fk_conversations_organization_id_producer_id_producers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_message_id"],
            ["inbound_messages.organization_id", "inbound_messages.id"],
            name=op.f("fk_conversations_organization_id_source_message_id_inbound_messages"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name=op.f("fk_conversations_organization_id_organizations"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.UniqueConstraint("organization_id", "id",
                            name=op.f("uq_conversations_organization_id_id")),
        sa.UniqueConstraint("organization_id", "producer_id",
                            name=op.f("uq_conversations_organization_id_producer_id")),
    )
    op.create_index(op.f("ix_conversations_organization_id"), "conversations",
                    ["organization_id"], unique=False)
    op.alter_column("lot_events", "lot_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    # Falla si ya hay eventos sin lote: hay que asignarles uno o borrarlos antes.
    op.alter_column("lot_events", "lot_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_index(op.f("ix_conversations_organization_id"), table_name="conversations")
    op.drop_table("conversations")
