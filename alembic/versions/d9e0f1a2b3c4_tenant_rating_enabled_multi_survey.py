"""tenant conversation_rating_enabled + allow multi ratings per conversation

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-08-21 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column(
            "conversation_rating_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    op.drop_constraint(
        "uq_conversation_ratings_tenant_account_conv",
        "conversation_ratings",
        type_="unique",
    )
    op.create_index(
        "ix_conversation_ratings_tenant_account_conv_created",
        "conversation_ratings",
        ["tenant_id", "messaging_account_id", "conversation_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_ratings_tenant_account_conv_created",
        table_name="conversation_ratings",
    )
    op.create_unique_constraint(
        "uq_conversation_ratings_tenant_account_conv",
        "conversation_ratings",
        ["tenant_id", "messaging_account_id", "conversation_id"],
    )
    op.drop_column("tenant", "conversation_rating_enabled")
