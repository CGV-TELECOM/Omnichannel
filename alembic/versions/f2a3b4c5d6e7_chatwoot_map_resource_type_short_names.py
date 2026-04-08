"""Đổi giá trị resource_type map Chatwoot sang tên ngắn: account, agent, agent_bot.

Revision ID: f2a3b4c5d6e7
Revises: e8f1a2b3c4d5
Create Date: 2026-04-07

"""
from typing import Sequence, Union

from alembic import op


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e8f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE chatwoot_legacy_map SET resource_type = 'account'
        WHERE resource_type = 'tenant_account'
        """
    )
    op.execute(
        """
        UPDATE chatwoot_legacy_map SET resource_type = 'agent'
        WHERE resource_type = 'tenant_agent'
        """
    )
    op.execute(
        """
        UPDATE chatwoot_legacy_map SET resource_type = 'agent_bot'
        WHERE resource_type = 'tenant_agent_bot'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE chatwoot_legacy_map SET resource_type = 'tenant_account'
        WHERE resource_type = 'account'
        """
    )
    op.execute(
        """
        UPDATE chatwoot_legacy_map SET resource_type = 'tenant_agent'
        WHERE resource_type = 'agent'
        """
    )
    op.execute(
        """
        UPDATE chatwoot_legacy_map SET resource_type = 'tenant_agent_bot'
        WHERE resource_type = 'agent_bot'
        """
    )
