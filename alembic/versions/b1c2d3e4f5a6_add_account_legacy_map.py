"""add account_legacy_map tenant chatwoot account

Revision ID: b1c2d3e4f5a6
Revises: 9fcad9421693
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "9fcad9421693"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_legacy_map",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", sa.UUID(), nullable=False),
        sa.Column("chat_woot_account_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid", name="uq_account_legacy_map_uuid"),
        sa.UniqueConstraint("chat_woot_account_id", name="uq_account_legacy_map_chatwoot"),
    )
    op.create_index("idx_account_legacy_map_uuid", "account_legacy_map", ["uuid"], unique=False)
    op.create_index(
        "idx_account_legacy_map_chatwoot",
        "account_legacy_map",
        ["chat_woot_account_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_account_legacy_map_chatwoot", table_name="account_legacy_map")
    op.drop_index("idx_account_legacy_map_uuid", table_name="account_legacy_map")
    op.drop_table("account_legacy_map")
