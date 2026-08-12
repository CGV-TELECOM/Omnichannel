"""drop tenant_id from roles, permissions, role_permissions

RBAC catalog dùng chung toàn hệ thống — tenant_id trên 3 bảng này không còn
ý nghĩa nghiệp vụ (isolate data nằm ở users/tickets/customers...).

Revision ID: f2a3b4c5d6e8
Revises: e1f2a3b4c5d6
Create Date: 2026-08-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f2a3b4c5d6e8"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("role_permissions", "tenant_id")
    op.drop_column("permissions", "tenant_id")
    op.drop_column("roles", "tenant_id")


def downgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "permissions",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "role_permissions",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
    )
