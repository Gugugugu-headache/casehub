"""initial

Revision ID: 0001_initial
Revises: 
Create Date: 2026-02-05 00:00:00.000000
"""
# CaseHub 初始库结构。
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 核心用户表。
    op.create_table(
        "admins",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("admin_no", sa.String(length=32), nullable=False, unique=True),
        sa.Column("username", sa.String(length=64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=64)),
        sa.Column("email", sa.String(length=128)),
        sa.Column("status", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "teachers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("teacher_no", sa.String(length=32), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=128)),
        sa.Column("status", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "classes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("class_code", sa.String(length=32), nullable=False),
        sa.Column("class_name", sa.String(length=64), nullable=False),
        sa.Column("teacher_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["teacher_id"], ["teachers.id"]),
        sa.UniqueConstraint("class_code", name="uq_classes_class_code"),
    )
    op.create_index("ix_classes_teacher_id", "classes", ["teacher_id"])

    op.create_table(
        "students",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("student_no", sa.String(length=32), nullable=False, unique=True),
        sa.Column("class_id", sa.BigInteger(), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=128)),
        sa.Column("status", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"]),
    )
    op.create_index("ix_students_class_id", "students", ["class_id"])

    # 知识库与文档存储。
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("class_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("ragflow_dataset_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"]),
        sa.UniqueConstraint("class_id", name="uq_knowledge_bases_class_id"),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kb_id", sa.BigInteger(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("uploader_student_id", sa.BigInteger()),
        sa.Column("uploader_teacher_id", sa.BigInteger()),
        sa.Column("uploader_admin_id", sa.BigInteger()),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("mime_type", sa.String(length=128)),
        sa.Column("ragflow_document_id", sa.String(length=64), unique=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                "embedded",
                name="document_status",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("storage_path", sa.String(length=512)),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"]),
        sa.ForeignKeyConstraint(["uploader_student_id"], ["students.id"]),
        sa.ForeignKeyConstraint(["uploader_teacher_id"], ["teachers.id"]),
        sa.ForeignKeyConstraint(["uploader_admin_id"], ["admins.id"]),
    )
    op.create_index("ix_documents_kb_id", "documents", ["kb_id"])
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_kb_id_filename", "documents", ["kb_id", "filename"])

    # 管理员审核与文档版本。
    op.create_table(
        "document_audits",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("reviewer_admin_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum("approved", "rejected", name="audit_decision"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=255)),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["reviewer_admin_id"], ["admins.id"]),
    )
    op.create_index("ix_document_audits_document_id", "document_audits", ["document_id"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
    )

    # 嵌入任务、对话与搜索日志。
    op.create_table(
        "embeddings_tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("triggered_by_teacher_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_method", sa.String(length=32), nullable=False, server_default="table"),
        sa.Column(
            "status",
            sa.Enum("queued", "running", "success", "failed", name="embedding_task_status"),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("ragflow_task_id", sa.String(length=64)),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("message", sa.Text()),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["triggered_by_teacher_id"], ["teachers.id"]),
    )
    op.create_index("ix_embeddings_tasks_document_id", "embeddings_tasks", ["document_id"])
    op.create_index("ix_embeddings_tasks_status", "embeddings_tasks", ["status"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("owner_teacher_id", sa.BigInteger()),
        sa.Column("owner_student_id", sa.BigInteger()),
        sa.Column("kb_id", sa.BigInteger()),
        sa.Column("model_name", sa.String(length=64)),
        sa.Column("top_n", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("similarity_threshold", sa.Numeric(4, 3), nullable=False, server_default="0.2"),
        sa.Column("show_citations", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("system_prompt", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_teacher_id"], ["teachers.id"]),
        sa.ForeignKeyConstraint(["owner_student_id"], ["students.id"]),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"]),
    )
    op.create_index("ix_conversations_owner_teacher_id", "conversations", ["owner_teacher_id"])
    op.create_index("ix_conversations_owner_student_id", "conversations", ["owner_student_id"])
    op.create_index("ix_conversations_kb_id", "conversations", ["kb_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "sender_role",
            sa.Enum("user", "assistant", "system", name="sender_role"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("reference", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    op.create_table(
        "search_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_teacher_id", sa.BigInteger()),
        sa.Column("user_student_id", sa.BigInteger()),
        sa.Column("kb_id", sa.BigInteger(), nullable=False),
        sa.Column("query", sa.String(length=512), nullable=False),
        sa.Column("result_count", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_teacher_id"], ["teachers.id"]),
        sa.ForeignKeyConstraint(["user_student_id"], ["students.id"]),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"]),
    )
    op.create_index("ix_search_logs_user_teacher_id", "search_logs", ["user_teacher_id"])
    op.create_index("ix_search_logs_user_student_id", "search_logs", ["user_student_id"])
    op.create_index("ix_search_logs_kb_id_created_at", "search_logs", ["kb_id", "created_at"])

    op.create_table(
        "ragflow_settings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("owner_teacher_id", sa.BigInteger()),
        sa.Column("owner_student_id", sa.BigInteger()),
        sa.Column("api_base", sa.String(length=255), nullable=False, server_default="http://localhost:8080"),
        sa.Column("api_key", sa.String(length=255), nullable=False),
        sa.Column("default_model", sa.String(length=64)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_teacher_id"], ["teachers.id"]),
        sa.ForeignKeyConstraint(["owner_student_id"], ["students.id"]),
    )


def downgrade() -> None:
    # 按依赖反序删除。
    op.drop_table("ragflow_settings")
    op.drop_index("ix_search_logs_kb_id_created_at", table_name="search_logs")
    op.drop_index("ix_search_logs_user_student_id", table_name="search_logs")
    op.drop_index("ix_search_logs_user_teacher_id", table_name="search_logs")
    op.drop_table("search_logs")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_kb_id", table_name="conversations")
    op.drop_index("ix_conversations_owner_student_id", table_name="conversations")
    op.drop_index("ix_conversations_owner_teacher_id", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_embeddings_tasks_status", table_name="embeddings_tasks")
    op.drop_index("ix_embeddings_tasks_document_id", table_name="embeddings_tasks")
    op.drop_table("embeddings_tasks")
    op.drop_table("document_versions")
    op.drop_index("ix_document_audits_document_id", table_name="document_audits")
    op.drop_table("document_audits")
    op.drop_index("ix_documents_kb_id_filename", table_name="documents")
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_kb_id", table_name="documents")
    op.drop_table("documents")
    op.drop_table("knowledge_bases")
    op.drop_index("ix_students_class_id", table_name="students")
    op.drop_table("students")
    op.drop_index("ix_classes_teacher_id", table_name="classes")
    op.drop_table("classes")
    op.drop_table("teachers")
    op.drop_table("admins")
