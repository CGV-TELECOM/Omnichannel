"""messaging_inbox_bindings for live-chat website_token → tenant

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d6
Create Date: 2026-09-03 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e0f1a2b3c4d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "messaging_inbox_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("messaging_account_id", sa.Integer(), nullable=False),
        sa.Column("inbox_id", sa.Integer(), nullable=False),
        sa.Column("website_token", sa.String(length=128), nullable=False),
        sa.Column("inbox_name", sa.String(length=255), nullable=True),
        sa.Column("channel_type", sa.String(length=64), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "website_token", name="uq_messaging_inbox_bindings_website_token"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "inbox_id",
            name="uq_messaging_inbox_bindings_tenant_inbox",
        ),
    )
    op.create_index(
        "ix_messaging_inbox_bindings_tenant_id",
        "messaging_inbox_bindings",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_messaging_inbox_bindings_id"),
        "messaging_inbox_bindings",
        ["id"],
        unique=False,
    )
    # Scope persona theo inbox (nullable = mọi kênh) — sẵn sàng phase 2
    op.add_column(
        "tenant_kg_agents",
        sa.Column("inbox_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_tenant_kg_agents_tenant_inbox",
        "tenant_kg_agents",
        ["tenant_id", "inbox_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_kg_agents_tenant_inbox", table_name="tenant_kg_agents")
    op.drop_column("tenant_kg_agents", "inbox_id")
    op.drop_index(
        op.f("ix_messaging_inbox_bindings_id"), table_name="messaging_inbox_bindings"
    )
    op.drop_index(
        "ix_messaging_inbox_bindings_tenant_id", table_name="messaging_inbox_bindings"
    )
    op.drop_table("messaging_inbox_bindings")
