"""users username unique per tenant

Login dùng username + tenant, nên unique toàn hệ thống là sai.
Đổi thành UniqueConstraint(username, tenant_id).

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e8
Create Date: 2026-08-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "f2a3b4c5d6e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Drop unique constraint chỉ trên username
    for uc in inspector.get_unique_constraints("users"):
        cols = list(uc.get("column_names") or [])
        name = uc.get("name")
        if name and cols == ["username"]:
            op.drop_constraint(name, "users", type_="unique")

    # Drop unique index chỉ trên username (nếu có)
    for idx in inspector.get_indexes("users"):
        cols = list(idx.get("column_names") or [])
        name = idx.get("name")
        if name and idx.get("unique") and cols == ["username"]:
            op.drop_index(name, table_name="users")

    inspector = sa.inspect(bind)
    existing = {uc["name"] for uc in inspector.get_unique_constraints("users")}
    if "uq_users_username_tenant" not in existing:
        op.create_unique_constraint(
            "uq_users_username_tenant",
            "users",
            ["username", "tenant_id"],
        )


def downgrade() -> None:
    op.drop_constraint("uq_users_username_tenant", "users", type_="unique")
    op.create_unique_constraint("users_username_key", "users", ["username"])
