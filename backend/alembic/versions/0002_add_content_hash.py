"""add content_hash to documents

Revision ID: 0002_add_content_hash
Revises: 0001_initial
Create Date: 2026-02-12 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_add_content_hash"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("content_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "content_hash")
