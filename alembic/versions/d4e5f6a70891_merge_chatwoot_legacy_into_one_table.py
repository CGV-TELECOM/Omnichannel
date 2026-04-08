"""Gộp user_legacy_map + account_legacy_map -> chatwoot_legacy_map

Revision ID: d4e5f6a70891
Revises: b1c2d3e4f5a6
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a70891"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chatwoot_legacy_map",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("local_uuid", sa.UUID(), nullable=False),
        sa.Column("chatwoot_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_type", "local_uuid", name="uq_chatwoot_legacy_local"
        ),
        sa.UniqueConstraint(
            "resource_type", "chatwoot_id", name="uq_chatwoot_legacy_remote"
        ),
    )
    op.create_index(
        "ix_chatwoot_legacy_type_local",
        "chatwoot_legacy_map",
        ["resource_type", "local_uuid"],
        unique=False,
    )
    op.create_index(
        "ix_chatwoot_legacy_type_remote",
        "chatwoot_legacy_map",
        ["resource_type", "chatwoot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_chatwoot_legacy_map_resource_type"),
        "chatwoot_legacy_map",
        ["resource_type"],
        unique=False,
    )

    op.execute(
        """
        INSERT INTO chatwoot_legacy_map (resource_type, local_uuid, chatwoot_id, created_at)
        SELECT 'user', uuid, chat_woot_id, created_at FROM user_legacy_map
        """
    )
    op.execute(
        """
        INSERT INTO chatwoot_legacy_map (resource_type, local_uuid, chatwoot_id, created_at)
        SELECT 'tenant_account', uuid, chat_woot_account_id, created_at FROM account_legacy_map
        """
    )

    op.drop_index("idx_account_legacy_map_chatwoot", table_name="account_legacy_map")
    op.drop_index("idx_account_legacy_map_uuid", table_name="account_legacy_map")
    op.drop_table("account_legacy_map")

    op.drop_index("idx_user_legacy_map_chatwoot", table_name="user_legacy_map")
    op.drop_index("idx_user_legacy_map_uuid", table_name="user_legacy_map")
    op.drop_table("user_legacy_map")


def downgrade() -> None:
    op.create_table(
        "user_legacy_map",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", sa.UUID(), nullable=False),
        sa.Column("chat_woot_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_woot_id", name="uq_user_legacy_map_chatwoot"),
        sa.UniqueConstraint("uuid", name="uq_user_legacy_map_uuid"),
    )
    op.create_index(
        "idx_user_legacy_map_chatwoot", "user_legacy_map", ["chat_woot_id"], unique=False
    )
    op.create_index(
        "idx_user_legacy_map_uuid", "user_legacy_map", ["uuid"], unique=False
    )

    op.create_table(
        "account_legacy_map",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", sa.UUID(), nullable=False),
        sa.Column("chat_woot_account_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chat_woot_account_id", name="uq_account_legacy_map_chatwoot"
        ),
        sa.UniqueConstraint("uuid", name="uq_account_legacy_map_uuid"),
    )
    op.create_index(
        "idx_account_legacy_map_chatwoot",
        "account_legacy_map",
        ["chat_woot_account_id"],
        unique=False,
    )
    op.create_index(
        "idx_account_legacy_map_uuid", "account_legacy_map", ["uuid"], unique=False
    )

    op.execute(
        """
        INSERT INTO user_legacy_map (uuid, chat_woot_id, created_at)
        SELECT local_uuid, chatwoot_id, created_at FROM chatwoot_legacy_map
        WHERE resource_type = 'user'
        """
    )
    op.execute(
        """
        INSERT INTO account_legacy_map (uuid, chat_woot_account_id, created_at)
        SELECT local_uuid, chatwoot_id, created_at FROM chatwoot_legacy_map
        WHERE resource_type = 'tenant_account'
        """
    )

    op.drop_index("ix_chatwoot_legacy_map_resource_type", table_name="chatwoot_legacy_map")
    op.drop_index("ix_chatwoot_legacy_type_remote", table_name="chatwoot_legacy_map")
    op.drop_index("ix_chatwoot_legacy_type_local", table_name="chatwoot_legacy_map")
    op.drop_table("chatwoot_legacy_map")
