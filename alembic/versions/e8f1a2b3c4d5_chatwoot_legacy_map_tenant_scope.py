"""Thêm tenant_id + unique theo tenant cho map Chatwoot (agent/bot).

Revision ID: e8f1a2b3c4d5
Revises: d4e5f6a70891
Create Date: 2026-04-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f1a2b3c4d5"
down_revision: Union[str, None] = "d4e5f6a70891"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chatwoot_legacy_map",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        "ix_chatwoot_legacy_map_tenant_id",
        "chatwoot_legacy_map",
        ["tenant_id"],
        unique=False,
    )

    op.execute(
        """
        UPDATE chatwoot_legacy_map
        SET tenant_id = local_uuid
        WHERE resource_type = 'tenant_account'
        """
    )

    op.drop_constraint("uq_chatwoot_legacy_remote", "chatwoot_legacy_map", type_="unique")
    op.drop_index("ix_chatwoot_legacy_type_remote", table_name="chatwoot_legacy_map")

    op.create_index(
        "uq_cwl_scoped_remote",
        "chatwoot_legacy_map",
        ["resource_type", "tenant_id", "chatwoot_id"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
    )
    op.create_index(
        "uq_cwl_user_remote",
        "chatwoot_legacy_map",
        ["resource_type", "chatwoot_id"],
        unique=True,
        postgresql_where=sa.text("resource_type = 'user'"),
    )
def downgrade() -> None:
    op.drop_index("uq_cwl_user_remote", table_name="chatwoot_legacy_map")
    op.drop_index("uq_cwl_scoped_remote", table_name="chatwoot_legacy_map")

    op.create_index(
        "ix_chatwoot_legacy_type_remote",
        "chatwoot_legacy_map",
        ["resource_type", "chatwoot_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_chatwoot_legacy_remote",
        "chatwoot_legacy_map",
        ["resource_type", "chatwoot_id"],
    )

    op.drop_index("ix_chatwoot_legacy_map_tenant_id", table_name="chatwoot_legacy_map")
    op.drop_column("chatwoot_legacy_map", "tenant_id")
