"""roles tenant scoped

Role thuộc tenant (admin-partner, user, custom) hoặc platform (admin, tenant_id NULL).
Backfill: clone admin-partner/user cho từng tenant, remap users, deactivate global copies.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-08-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TENANT_ROLE_NAMES = ("admin-partner", "user")


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_roles_tenant_id", "roles", ["tenant_id"], unique=False)

    conn = op.get_bind()

    # Platform role admin stays tenant_id NULL
    conn.execute(
        sa.text(
            """
            UPDATE roles
            SET tenant_id = NULL
            WHERE lower(name) = 'admin'
            """
        )
    )

    # Clone shared tenant roles (admin-partner, user) per active tenant
    tenants = conn.execute(
        sa.text("SELECT id FROM tenant WHERE is_active = 1 OR is_active IS NULL")
    ).fetchall()
    # Fallback: all tenants if is_active filter yields none
    if not tenants:
        tenants = conn.execute(sa.text("SELECT id FROM tenant")).fetchall()

    for (tenant_id,) in tenants:
        for role_name in TENANT_ROLE_NAMES:
            src = conn.execute(
                sa.text(
                    """
                    SELECT id, name, description, role_order, is_active
                    FROM roles
                    WHERE lower(name) = lower(:name)
                      AND tenant_id IS NULL
                    LIMIT 1
                    """
                ),
                {"name": role_name},
            ).fetchone()
            if not src:
                continue

            src_id, name, description, role_order, is_active = src

            existing = conn.execute(
                sa.text(
                    """
                    SELECT id FROM roles
                    WHERE lower(name) = lower(:name) AND tenant_id = :tenant_id
                    LIMIT 1
                    """
                ),
                {"name": role_name, "tenant_id": tenant_id},
            ).fetchone()
            if existing:
                new_role_id = existing[0]
            else:
                new_role_id = conn.execute(
                    sa.text(
                        """
                        INSERT INTO roles (id, name, description, role_order, is_active, tenant_id)
                        VALUES (gen_random_uuid(), :name, :description, :role_order, :is_active, :tenant_id)
                        RETURNING id
                        """
                    ),
                    {
                        "name": name,
                        "description": description,
                        "role_order": role_order,
                        "is_active": is_active if is_active is not None else 1,
                        "tenant_id": tenant_id,
                    },
                ).scalar()

                # Copy role_permissions from source global role
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO role_permissions (id, role_id, permission_id)
                        SELECT gen_random_uuid(), :new_role_id, rp.permission_id
                        FROM role_permissions rp
                        WHERE rp.role_id = :src_role_id
                          AND NOT EXISTS (
                            SELECT 1 FROM role_permissions x
                            WHERE x.role_id = :new_role_id
                              AND x.permission_id = rp.permission_id
                          )
                        """
                    ),
                    {"new_role_id": new_role_id, "src_role_id": src_id},
                )

            # Remap users in this tenant from global role → tenant clone
            conn.execute(
                sa.text(
                    """
                    UPDATE users
                    SET role_id = :new_role_id
                    WHERE tenant_id = :tenant_id
                      AND role_id = :src_role_id
                    """
                ),
                {
                    "new_role_id": new_role_id,
                    "tenant_id": tenant_id,
                    "src_role_id": src_id,
                },
            )

    # Deactivate leftover global admin-partner / user templates
    conn.execute(
        sa.text(
            """
            UPDATE roles
            SET is_active = 0
            WHERE tenant_id IS NULL
              AND lower(name) IN ('admin-partner', 'user')
            """
        )
    )

    # Unique indexes
    op.create_index(
        "uq_roles_tenant_name",
        "roles",
        ["tenant_id", "name"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
    )
    op.create_index(
        "uq_roles_platform_name",
        "roles",
        ["name"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_roles_platform_name", table_name="roles")
    op.drop_index("uq_roles_tenant_name", table_name="roles")
    op.drop_index("ix_roles_tenant_id", table_name="roles")
    op.drop_column("roles", "tenant_id")
