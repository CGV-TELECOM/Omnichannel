"""tenant_kg_agents relational table; drop tenant.agent_id

Revision ID: e0f1a2b3c4d6
Revises: d9e0f1a2b3c4
Create Date: 2026-08-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e0f1a2b3c4d6"
down_revision: Union[str, None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant_kg_agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kg_agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("graph_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("key", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        sa.UniqueConstraint("tenant_id", "key", name="uq_tenant_kg_agents_tenant_key"),
    )
    op.create_index("ix_tenant_kg_agents_tenant_id", "tenant_kg_agents", ["tenant_id"])
    op.create_index("ix_tenant_kg_agents_kg_agent_id", "tenant_kg_agents", ["kg_agent_id"])
    op.create_index(op.f("ix_tenant_kg_agents_id"), "tenant_kg_agents", ["id"], unique=False)

    op.execute(
        """
        INSERT INTO tenant_kg_agents (
            id, tenant_id, kg_agent_id, graph_id, key, label, is_default, is_active,
            created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            t.id,
            t.agent_id,
            t.graph_id,
            'default',
            'Default',
            true,
            true,
            now(),
            now()
        FROM tenant t
        WHERE t.agent_id IS NOT NULL
        """
    )

    op.drop_column("tenant", "agent_id")


def downgrade() -> None:
    op.add_column("tenant", sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(
        """
        UPDATE tenant t
        SET agent_id = sub.kg_agent_id
        FROM (
            SELECT DISTINCT ON (tenant_id) tenant_id, kg_agent_id
            FROM tenant_kg_agents
            WHERE is_default = true
            ORDER BY tenant_id, created_at
        ) sub
        WHERE t.id = sub.tenant_id
        """
    )
    op.execute(
        """
        UPDATE tenant t
        SET agent_id = sub.kg_agent_id
        FROM (
            SELECT DISTINCT ON (tenant_id) tenant_id, kg_agent_id
            FROM tenant_kg_agents
            ORDER BY tenant_id, created_at
        ) sub
        WHERE t.id = sub.tenant_id AND t.agent_id IS NULL
        """
    )
    op.drop_index(op.f("ix_tenant_kg_agents_id"), table_name="tenant_kg_agents")
    op.drop_index("ix_tenant_kg_agents_kg_agent_id", table_name="tenant_kg_agents")
    op.drop_index("ix_tenant_kg_agents_tenant_id", table_name="tenant_kg_agents")
    op.drop_table("tenant_kg_agents")
