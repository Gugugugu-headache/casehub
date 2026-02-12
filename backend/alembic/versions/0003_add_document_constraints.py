"""add document unique constraint and hash index

Revision ID: 0003_add_document_constraints
Revises: 0002_add_content_hash
Create Date: 2026-02-12 00:00:00.000000
"""

from alembic import op

revision = "0003_add_document_constraints"
down_revision = "0002_add_content_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_documents_kb_id_content_hash",
        "documents",
        ["kb_id", "content_hash"],
    )
    op.create_unique_constraint(
        "uq_documents_kb_original_name",
        "documents",
        ["kb_id", "original_name"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_documents_kb_original_name",
        "documents",
        type_="unique",
    )
    op.drop_index(
        "ix_documents_kb_id_content_hash",
        table_name="documents",
    )
