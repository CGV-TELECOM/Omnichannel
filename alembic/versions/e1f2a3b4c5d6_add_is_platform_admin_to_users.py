"""add is_platform_admin to users

Tách "platform admin" (CGV ops, được cross-tenant) khỏi "tenant admin"
(level/role cao nhất trong tenant nhưng không được bypass tenant).

Backfill: user đang giữ level_order == max toàn hệ thống được coi là platform admin
để giữ nguyên hành vi hiện tại; sau đó tắt flag thủ công cho các admin tenant nếu cần.

Revision ID: e1f2a3b4c5d6
Revises: c7a8b9d0e1f2
Create Date: 2026-08-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "c7a8b9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_platform_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Giữ hành vi hiện tại: user có level cao nhất toàn hệ thống -> platform admin.
    op.execute(
        """
        UPDATE users
        SET is_platform_admin = true
        WHERE level_id IN (
            SELECT id FROM levels
            WHERE level_order = (SELECT MAX(level_order) FROM levels)
        )
        """
    )


def downgrade() -> None:
    op.drop_column("users", "is_platform_admin")
