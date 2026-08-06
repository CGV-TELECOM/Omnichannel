"""call_log_events + enrich call_logs for telephony webhooks

Revision ID: c7a8b9d0e1f2
Revises: abc439aefe19
Create Date: 2026-08-05 14:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c7a8b9d0e1f2"
down_revision: Union[str, None] = "abc439aefe19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- call_logs: chuẩn hóa sip_call_id → UUID (giữ legacy trong meta_data) ---
    op.execute(
        """
        UPDATE call_logs
        SET meta_data = COALESCE(meta_data, '{}'::jsonb)
            || jsonb_build_object('legacy_sip_call_id', sip_call_id),
            sip_call_id = gen_random_uuid()::text
        WHERE sip_call_id IS NULL
           OR sip_call_id !~* '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        """
    )
    op.execute(
        """
        ALTER TABLE call_logs
        ALTER COLUMN sip_call_id TYPE UUID
        USING sip_call_id::uuid
        """
    )

    op.add_column("call_logs", sa.Column("provider_call_id", sa.UUID(), nullable=True))
    op.add_column("call_logs", sa.Column("from_number", sa.String(length=30), nullable=True))
    op.add_column("call_logs", sa.Column("to_number", sa.String(length=30), nullable=True))
    op.add_column("call_logs", sa.Column("hotline", sa.String(length=30), nullable=True))
    op.add_column(
        "call_logs",
        sa.Column("source", sa.String(length=20), nullable=False, server_default="web"),
    )
    op.add_column("call_logs", sa.Column("answered_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column(
        "call_logs",
        sa.Column("billsec", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "call_logs",
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_index("ix_call_logs_provider_call_id", "call_logs", ["provider_call_id"], unique=False)
    op.create_index("ix_call_logs_from_number", "call_logs", ["from_number"], unique=False)
    op.create_index("ix_call_logs_to_number", "call_logs", ["to_number"], unique=False)
    op.create_index("ix_call_logs_hotline", "call_logs", ["hotline"], unique=False)
    op.create_index("ix_call_logs_status", "call_logs", ["status"], unique=False)
    op.create_index("ix_call_logs_started_at", "call_logs", ["started_at"], unique=False)
    op.create_index("ix_call_logs_tenant_started", "call_logs", ["tenant_id", "started_at"], unique=False)
    op.create_index(
        "ix_call_logs_tenant_direction_status_started",
        "call_logs",
        ["tenant_id", "direction", "status", "started_at"],
        unique=False,
    )

    # --- call_log_events ---
    op.create_table(
        "call_log_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("call_log_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("sip_call_id", sa.UUID(), nullable=False),
        sa.Column("provider_call_id", sa.UUID(), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=False),
        sa.Column("application", sa.String(length=50), nullable=True),
        sa.Column("event_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(["call_log_id"], ["call_logs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_call_log_events_id", "call_log_events", ["id"], unique=False)
    op.create_index("ix_call_log_events_call_log_id", "call_log_events", ["call_log_id"], unique=False)
    op.create_index("ix_call_log_events_tenant_id", "call_log_events", ["tenant_id"], unique=False)
    op.create_index("ix_call_log_events_sip_call_id", "call_log_events", ["sip_call_id"], unique=False)
    op.create_index("ix_call_log_events_provider_call_id", "call_log_events", ["provider_call_id"], unique=False)
    op.create_index("ix_call_log_events_state", "call_log_events", ["state"], unique=False)
    op.create_index("ix_call_log_events_received_at", "call_log_events", ["received_at"], unique=False)
    op.create_index(
        "ix_call_log_events_sip_received",
        "call_log_events",
        ["sip_call_id", "received_at"],
        unique=False,
    )
    op.create_index(
        "ix_call_log_events_tenant_state_received",
        "call_log_events",
        ["tenant_id", "state", "received_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_call_log_events_tenant_state_received", table_name="call_log_events")
    op.drop_index("ix_call_log_events_sip_received", table_name="call_log_events")
    op.drop_index("ix_call_log_events_received_at", table_name="call_log_events")
    op.drop_index("ix_call_log_events_state", table_name="call_log_events")
    op.drop_index("ix_call_log_events_provider_call_id", table_name="call_log_events")
    op.drop_index("ix_call_log_events_sip_call_id", table_name="call_log_events")
    op.drop_index("ix_call_log_events_tenant_id", table_name="call_log_events")
    op.drop_index("ix_call_log_events_call_log_id", table_name="call_log_events")
    op.drop_index("ix_call_log_events_id", table_name="call_log_events")
    op.drop_table("call_log_events")

    op.drop_index("ix_call_logs_tenant_direction_status_started", table_name="call_logs")
    op.drop_index("ix_call_logs_tenant_started", table_name="call_logs")
    op.drop_index("ix_call_logs_started_at", table_name="call_logs")
    op.drop_index("ix_call_logs_status", table_name="call_logs")
    op.drop_index("ix_call_logs_hotline", table_name="call_logs")
    op.drop_index("ix_call_logs_to_number", table_name="call_logs")
    op.drop_index("ix_call_logs_from_number", table_name="call_logs")
    op.drop_index("ix_call_logs_provider_call_id", table_name="call_logs")

    op.drop_column("call_logs", "updated_at")
    op.drop_column("call_logs", "billsec")
    op.drop_column("call_logs", "answered_at")
    op.drop_column("call_logs", "source")
    op.drop_column("call_logs", "hotline")
    op.drop_column("call_logs", "to_number")
    op.drop_column("call_logs", "from_number")
    op.drop_column("call_logs", "provider_call_id")

    op.execute(
        """
        ALTER TABLE call_logs
        ALTER COLUMN sip_call_id TYPE VARCHAR(255)
        USING sip_call_id::text
        """
    )
