"""add conversation_ratings for omnichannel CSAT MVP

Revision ID: c8d9e0f1a2b3
Revises: b4c5d6e7f8a9
Create Date: 2026-08-21 10:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_ratings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("messaging_account_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=100), nullable=True),
        sa.Column("inbox_id", sa.Integer(), nullable=True),
        sa.Column("agent_chatwoot_id", sa.Integer(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("rating_url", sa.String(length=512), nullable=True),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("meta_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "messaging_account_id",
            "conversation_id",
            name="uq_conversation_ratings_tenant_account_conv",
        ),
    )
    op.create_index(
        "ix_conversation_ratings_id", "conversation_ratings", ["id"], unique=False
    )
    op.create_index(
        "ix_conversation_ratings_tenant_id",
        "conversation_ratings",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_ratings_messaging_account_id",
        "conversation_ratings",
        ["messaging_account_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_ratings_conversation_id",
        "conversation_ratings",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_ratings_channel",
        "conversation_ratings",
        ["channel"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_ratings_status",
        "conversation_ratings",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_ratings_token",
        "conversation_ratings",
        ["token"],
        unique=True,
    )
    op.create_index(
        "ix_conversation_ratings_tenant_status_created",
        "conversation_ratings",
        ["tenant_id", "status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_ratings_tenant_status_created",
        table_name="conversation_ratings",
    )
    op.drop_index("ix_conversation_ratings_token", table_name="conversation_ratings")
    op.drop_index("ix_conversation_ratings_status", table_name="conversation_ratings")
    op.drop_index("ix_conversation_ratings_channel", table_name="conversation_ratings")
    op.drop_index(
        "ix_conversation_ratings_conversation_id", table_name="conversation_ratings"
    )
    op.drop_index(
        "ix_conversation_ratings_messaging_account_id",
        table_name="conversation_ratings",
    )
    op.drop_index(
        "ix_conversation_ratings_tenant_id", table_name="conversation_ratings"
    )
    op.drop_index("ix_conversation_ratings_id", table_name="conversation_ratings")
    op.drop_table("conversation_ratings")
