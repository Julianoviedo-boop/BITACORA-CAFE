"""Canal de WhatsApp y consentimiento

- organizations.whatsapp_address: número que recibe los mensajes de la organización
  (en OpenBSP, el phone_number_id). Único: un número es de una sola organización.
- inbound_messages.message_type admite 'imagen'.
- inbound_messages.processing_status admite 'sin_consentimiento'.
- inbound_messages.media_ref pasa a TEXT (las referencias de OpenBSP pueden ser largas).

Revision ID: b7c41e9d2f03
Revises: 4ad5d8a6a7ec
Create Date: 2026-11-12 23:50:00+00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c41e9d2f03"
down_revision: str | None = "4ad5d8a6a7ec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = "message_type IN ('texto', 'audio', 'otro')"
NEW_TYPES = "message_type IN ('texto', 'audio', 'imagen', 'otro')"
OLD_STATUSES = (
    "processing_status IN ('recibido', 'transcrito', 'extraido', 'procesado', 'error')"
)
NEW_STATUSES = (
    "processing_status IN ('recibido', 'transcrito', 'extraido', 'procesado', "
    "'sin_consentimiento', 'error')"
)


def _replace_check(name: str, condition: str) -> None:
    op.drop_constraint(op.f(f"ck_inbound_messages_{name}"), "inbound_messages", type_="check")
    op.create_check_constraint(op.f(f"ck_inbound_messages_{name}"), "inbound_messages", condition)


def upgrade() -> None:
    op.add_column("organizations", sa.Column("whatsapp_address", sa.String(64), nullable=True))
    op.create_unique_constraint(
        op.f("uq_organizations_whatsapp_address"), "organizations", ["whatsapp_address"]
    )
    _replace_check("message_type", NEW_TYPES)
    _replace_check("processing_status", NEW_STATUSES)
    op.alter_column(
        "inbound_messages", "media_ref", type_=sa.Text(), existing_type=sa.String(512)
    )


def downgrade() -> None:
    op.alter_column(
        "inbound_messages", "media_ref", type_=sa.String(512), existing_type=sa.Text()
    )
    _replace_check("processing_status", OLD_STATUSES)
    _replace_check("message_type", OLD_TYPES)
    op.drop_constraint(
        op.f("uq_organizations_whatsapp_address"), "organizations", type_="unique"
    )
    op.drop_column("organizations", "whatsapp_address")
