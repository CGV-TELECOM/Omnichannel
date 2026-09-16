"""Add contact_capture JSONB on messaging_inbox_bindings.

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-09-15

OmniHub source of truth for livechat contact policy (mode=bot, labels, …).
Chatwoot chỉ mirror pre_chat_form_* trên Channel::WebWidget.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messaging_inbox_bindings",
        sa.Column(
            "contact_capture",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("messaging_inbox_bindings", "contact_capture")
