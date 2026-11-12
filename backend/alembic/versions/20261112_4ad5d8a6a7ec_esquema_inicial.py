"""Esquema inicial

Organizaciones, fincas, productores, lotes, mensajes, eventos, alertas y reportes. Todas las tablas de negocio llevan organization_id
indexado y FKs compuestas (organization_id, id) para impedir cruces entre
organizaciones a nivel de base de datos.

Revision ID: 4ad5d8a6a7ec
Revises: 
Create Date: 2026-11-12 23:07:36.428277+00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '4ad5d8a6a7ec'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('organizations',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('slug', sa.String(length=80), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organizations')),
    sa.UniqueConstraint('slug', name=op.f('uq_organizations_slug'))
    )
    op.create_table('farms',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('municipality', sa.String(length=120), nullable=True),
    sa.Column('department', sa.String(length=120), nullable=True),
    sa.Column('altitude_m', sa.Integer(), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_farms_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_farms')),
    sa.UniqueConstraint('organization_id', 'id', name=op.f('uq_farms_organization_id_id'))
    )
    op.create_index(op.f('ix_farms_organization_id'), 'farms', ['organization_id'], unique=False)
    op.create_table('lots',
    sa.Column('farm_id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('variety', sa.String(length=80), nullable=True),
    sa.Column('process', sa.String(length=80), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='activo', nullable=False),
    sa.Column('public_token', sa.String(length=64), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("status IN ('activo', 'inactivo')", name=op.f('ck_lots_status')),
    sa.ForeignKeyConstraint(['organization_id', 'farm_id'], ['farms.organization_id', 'farms.id'], name=op.f('fk_lots_organization_id_farm_id_farms'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_lots_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_lots')),
    sa.UniqueConstraint('organization_id', 'code', name=op.f('uq_lots_organization_id_code')),
    sa.UniqueConstraint('organization_id', 'id', name=op.f('uq_lots_organization_id_id')),
    sa.UniqueConstraint('public_token', name=op.f('uq_lots_public_token'))
    )
    op.create_index(op.f('ix_lots_organization_id'), 'lots', ['organization_id'], unique=False)
    op.create_table('producers',
    sa.Column('farm_id', sa.Uuid(), nullable=True),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=False),
    sa.Column('consent_status', sa.String(length=20), server_default='pendiente', nullable=False),
    sa.Column('consent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("consent_status IN ('pendiente', 'aceptado', 'revocado')", name=op.f('ck_producers_consent_status')),
    sa.ForeignKeyConstraint(['organization_id', 'farm_id'], ['farms.organization_id', 'farms.id'], name=op.f('fk_producers_organization_id_farm_id_farms'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_producers_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_producers')),
    sa.UniqueConstraint('organization_id', 'id', name=op.f('uq_producers_organization_id_id')),
    sa.UniqueConstraint('organization_id', 'phone', name=op.f('uq_producers_organization_id_phone'))
    )
    op.create_index(op.f('ix_producers_organization_id'), 'producers', ['organization_id'], unique=False)
    op.create_table('inbound_messages',
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('producer_id', sa.Uuid(), nullable=True),
    sa.Column('from_phone', sa.String(length=20), nullable=False),
    sa.Column('message_type', sa.String(length=10), nullable=False),
    sa.Column('text_body', sa.Text(), nullable=True),
    sa.Column('media_ref', sa.String(length=512), nullable=True),
    sa.Column('transcription', sa.Text(), nullable=True),
    sa.Column('processing_status', sa.String(length=20), server_default='recibido', nullable=False),
    sa.Column('error_detail', sa.Text(), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("message_type IN ('texto', 'audio', 'otro')", name=op.f('ck_inbound_messages_message_type')),
    sa.CheckConstraint("processing_status IN ('recibido', 'transcrito', 'extraido', 'procesado', 'error')", name=op.f('ck_inbound_messages_processing_status')),
    sa.ForeignKeyConstraint(['organization_id', 'producer_id'], ['producers.organization_id', 'producers.id'], name=op.f('fk_inbound_messages_organization_id_producer_id_producers'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_inbound_messages_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_inbound_messages')),
    sa.UniqueConstraint('external_id', name=op.f('uq_inbound_messages_external_id')),
    sa.UniqueConstraint('organization_id', 'id', name=op.f('uq_inbound_messages_organization_id_id'))
    )
    op.create_index(op.f('ix_inbound_messages_organization_id'), 'inbound_messages', ['organization_id'], unique=False)
    op.create_table('reports',
    sa.Column('lot_id', sa.Uuid(), nullable=False),
    sa.Column('date_from', sa.Date(), nullable=False),
    sa.Column('date_to', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='pendiente', nullable=False),
    sa.Column('file_ref', sa.String(length=512), nullable=True),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("status IN ('pendiente', 'generado', 'error')", name=op.f('ck_reports_status')),
    sa.CheckConstraint('date_from <= date_to', name=op.f('ck_reports_date_range')),
    sa.ForeignKeyConstraint(['organization_id', 'lot_id'], ['lots.organization_id', 'lots.id'], name=op.f('fk_reports_organization_id_lot_id_lots'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_reports_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reports')),
    sa.UniqueConstraint('organization_id', 'id', name=op.f('uq_reports_organization_id_id'))
    )
    op.create_index(op.f('ix_reports_organization_id'), 'reports', ['organization_id'], unique=False)
    op.create_table('lot_events',
    sa.Column('lot_id', sa.Uuid(), nullable=False),
    sa.Column('message_id', sa.Uuid(), nullable=True),
    sa.Column('event_type', sa.String(length=50), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('original_text', sa.Text(), nullable=True),
    sa.Column('confidence', sa.Numeric(precision=4, scale=3), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='pendiente', nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("status IN ('pendiente', 'confirmado', 'rechazado')", name=op.f('ck_lot_events_status')),
    sa.CheckConstraint('confidence IS NULL OR confidence BETWEEN 0 AND 1', name=op.f('ck_lot_events_confidence')),
    sa.ForeignKeyConstraint(['organization_id', 'lot_id'], ['lots.organization_id', 'lots.id'], name=op.f('fk_lot_events_organization_id_lot_id_lots'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'message_id'], ['inbound_messages.organization_id', 'inbound_messages.id'], name=op.f('fk_lot_events_organization_id_message_id_inbound_messages'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_lot_events_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_lot_events')),
    sa.UniqueConstraint('organization_id', 'id', name=op.f('uq_lot_events_organization_id_id'))
    )
    op.create_index('ix_lot_events_data', 'lot_events', ['data'], unique=False, postgresql_using='gin', postgresql_ops={'data': 'jsonb_path_ops'})
    op.create_index('ix_lot_events_org_lot_occurred', 'lot_events', ['organization_id', 'lot_id', 'occurred_at'], unique=False)
    op.create_index(op.f('ix_lot_events_organization_id'), 'lot_events', ['organization_id'], unique=False)
    op.create_table('alerts',
    sa.Column('lot_id', sa.Uuid(), nullable=False),
    sa.Column('event_id', sa.Uuid(), nullable=True),
    sa.Column('rule_code', sa.String(length=80), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='pendiente', nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("status IN ('pendiente', 'enviada', 'descartada')", name=op.f('ck_alerts_status')),
    sa.ForeignKeyConstraint(['organization_id', 'event_id'], ['lot_events.organization_id', 'lot_events.id'], name=op.f('fk_alerts_organization_id_event_id_lot_events'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'lot_id'], ['lots.organization_id', 'lots.id'], name=op.f('fk_alerts_organization_id_lot_id_lots'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_alerts_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alerts')),
    sa.UniqueConstraint('organization_id', 'id', name=op.f('uq_alerts_organization_id_id'))
    )
    op.create_index(op.f('ix_alerts_organization_id'), 'alerts', ['organization_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_alerts_organization_id'), table_name='alerts')
    op.drop_table('alerts')
    op.drop_index(op.f('ix_lot_events_organization_id'), table_name='lot_events')
    op.drop_index('ix_lot_events_org_lot_occurred', table_name='lot_events')
    op.drop_index('ix_lot_events_data', table_name='lot_events', postgresql_using='gin', postgresql_ops={'data': 'jsonb_path_ops'})
    op.drop_table('lot_events')
    op.drop_index(op.f('ix_reports_organization_id'), table_name='reports')
    op.drop_table('reports')
    op.drop_index(op.f('ix_inbound_messages_organization_id'), table_name='inbound_messages')
    op.drop_table('inbound_messages')
    op.drop_index(op.f('ix_producers_organization_id'), table_name='producers')
    op.drop_table('producers')
    op.drop_index(op.f('ix_lots_organization_id'), table_name='lots')
    op.drop_table('lots')
    op.drop_index(op.f('ix_farms_organization_id'), table_name='farms')
    op.drop_table('farms')
    op.drop_table('organizations')
