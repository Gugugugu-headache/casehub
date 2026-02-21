"""add name to conversations

Revision ID: 0005_add_conversation_name
Revises: 0004_add_ragflow_conversation_fields
Create Date: 2026-02-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_add_conversation_name"
down_revision = "0004_add_ragflow_conversation_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("name", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "name")
