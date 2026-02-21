from __future__ import annotations

"""CaseHub 的 SQLAlchemy ORM 模型。

用于定义数据库表结构、关系与枚举类型。
"""

import enum
from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    BigInteger,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    Numeric,
    JSON,
    ForeignKey,
    UniqueConstraint,
    Index,
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class DocumentStatus(str, enum.Enum):
    """文档在审核/嵌入流程中的状态枚举。"""

    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    embedded = "embedded"


class AuditDecision(str, enum.Enum):
    """管理员对上传文档的审核结论。"""
    approved = "approved"
    rejected = "rejected"


class EmbeddingTaskStatus(str, enum.Enum):
    """异步嵌入任务状态。"""
    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"


class SenderRole(str, enum.Enum):
    """对话消息发送者角色。"""
    user = "user"
    assistant = "assistant"
    system = "system"


# --- 账号与用户 ---
class Admin(Base):
    __tablename__ = "admins"

    # 说明：管理员账号表，支持账号/工号登陆
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)  # 主键
    admin_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # 管理员编号（登陆用）
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # 备用用户名
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)  # 密码哈希
    name: Mapped[Optional[str]] = mapped_column(String(64))  # 显示姓名
    email: Mapped[Optional[str]] = mapped_column(String(128))  # 邮箱
    status: Mapped[int] = mapped_column(default=1)  # 1 正常，0 停用
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Teacher(Base):
    __tablename__ = "teachers"

    # 教师账号表：工号登陆
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)  # 主键
    teacher_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # 教师工号
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)  # 密码哈希
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # 姓名
    email: Mapped[Optional[str]] = mapped_column(String(128))  # 邮箱
    status: Mapped[int] = mapped_column(default=1)  # 1 正常，0 停用
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )

    classes: Mapped[List["Class"]] = relationship(back_populates="teacher")  # 一个教师可对应多个班级


# --- 班级与知识库 ---
class Class(Base):
    __tablename__ = "classes"
    __table_args__ = (UniqueConstraint("class_code"),)

    # 班级表：一个班级只对应一位教师
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)  # 主键
    class_code: Mapped[str] = mapped_column(String(32), nullable=False)  # 班级编号
    class_name: Mapped[str] = mapped_column(String(64), nullable=False)  # 班级名称
    teacher_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("teachers.id"), nullable=False, index=True
    )  # 任课教师
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )

    teacher: Mapped[Teacher] = relationship(back_populates="classes")  # 反向：教师 -> 班级
    students: Mapped[List["Student"]] = relationship(back_populates="class_")  # 一个班包含多个学生
    knowledge_base: Mapped[Optional["KnowledgeBase"]] = relationship(
        back_populates="class_", uselist=False
    )  # 一班一库（uselist=False）


class Student(Base):
    __tablename__ = "students"

    # 学生账号表：学号登陆
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)  # 主键
    student_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # 学号
    class_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("classes.id"), nullable=False, index=True
    )  # 隶属班级（多对一）
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)  # 密码哈希
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # 姓名
    email: Mapped[Optional[str]] = mapped_column(String(128))  # 邮箱
    status: Mapped[int] = mapped_column(default=1)  # 1 正常，0 停用
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )

    class_: Mapped[Class] = relationship(back_populates="students")  # 反向关系


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("class_id"),)

    # 知识库表：一班一库，记录 ragflow dataset 对应关系
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)  # 主键
    class_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("classes.id"), nullable=False
    )  # 对应班级
    name: Mapped[str] = mapped_column(String(128), nullable=False)  # 知识库名称
    description: Mapped[Optional[str]] = mapped_column(Text)  # 描述
    ragflow_dataset_id: Mapped[str] = mapped_column(String(64), nullable=False)  # ragflow dataset id
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )

    class_: Mapped[Class] = relationship(back_populates="knowledge_base")  # 反向关系：班级 -> 知识库
    documents: Mapped[List["Document"]] = relationship(back_populates="kb")  # 知识库下的文件列表


# --- 文档与审核 ---
class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_kb_id_filename", "kb_id", "filename"),
        Index("ix_documents_kb_id_content_hash", "kb_id", "content_hash"),
        UniqueConstraint("kb_id", "original_name", name="uq_documents_kb_original_name"),
    )

    # 文档表：记录文件元数据与上传者
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)  # 主键
    kb_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("knowledge_bases.id"), nullable=False, index=True
    )  # 所属知识库
    filename: Mapped[str] = mapped_column(String(255), nullable=False)  # 存储用文件名/对象键
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)  # 原始文件名
    uploader_student_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("students.id"), nullable=True
    )  # 学生上传者（学生上传时填）
    uploader_teacher_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("teachers.id"), nullable=True
    )  # 教师上传者
    uploader_admin_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("admins.id"), nullable=True
    )  # 管理员上传者
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)  # 文件大小
    mime_type: Mapped[Optional[str]] = mapped_column(String(128))  # MIME 类型
    content_hash: Mapped[Optional[str]] = mapped_column(String(64))  # 文件内容 SHA256
    ragflow_document_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)  # ragflow 返回的 doc id
    status: Mapped[DocumentStatus] = mapped_column(
        SAEnum(DocumentStatus), default=DocumentStatus.pending, nullable=False, index=True
    )  # 审核/嵌入状态
    storage_path: Mapped[Optional[str]] = mapped_column(String(512))  # 对象存储路径/键
    uploaded_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )

    kb: Mapped[KnowledgeBase] = relationship(back_populates="documents")  # 反向：知识库 -> 文档列表
    uploader_student: Mapped[Optional[Student]] = relationship()  # 可能为学生
    uploader_teacher: Mapped[Optional[Teacher]] = relationship()  # 可能为教师
    uploader_admin: Mapped[Optional[Admin]] = relationship()  # 可能为管理员

    audits: Mapped[List["DocumentAudit"]] = relationship(back_populates="document")
    versions: Mapped[List["DocumentVersion"]] = relationship(back_populates="document")
    embedding_tasks: Mapped[List["EmbeddingTask"]] = relationship(back_populates="document")


class DocumentAudit(Base):
    __tablename__ = "document_audits"
    __table_args__ = (Index("ix_document_audits_document_id", "document_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id"), nullable=False
    )
    reviewer_admin_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("admins.id"), nullable=False
    )
    decision: Mapped[AuditDecision] = mapped_column(
        SAEnum(AuditDecision), nullable=False
    )
    reason: Mapped[Optional[str]] = mapped_column(String(255))
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    document: Mapped[Document] = relationship(back_populates="audits")
    reviewer_admin: Mapped[Admin] = relationship()


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    document: Mapped[Document] = relationship(back_populates="versions")


# --- 嵌入流程 ---
class EmbeddingTask(Base):
    __tablename__ = "embeddings_tasks"
    __table_args__ = (
        Index("ix_embeddings_tasks_document_id", "document_id"),
        Index("ix_embeddings_tasks_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id"), nullable=False
    )
    triggered_by_teacher_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("teachers.id"), nullable=False
    )
    chunk_method: Mapped[str] = mapped_column(String(32), default="table")
    status: Mapped[EmbeddingTaskStatus] = mapped_column(
        SAEnum(EmbeddingTaskStatus), default=EmbeddingTaskStatus.queued
    )
    ragflow_task_id: Mapped[Optional[str]] = mapped_column(String(64))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    message: Mapped[Optional[str]] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates="embedding_tasks")
    triggered_by_teacher: Mapped[Teacher] = relationship()


# --- 对话 ---
class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_owner_teacher_id", "owner_teacher_id"),
        Index("ix_conversations_owner_student_id", "owner_student_id"),
        Index("ix_conversations_kb_id", "kb_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_teacher_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("teachers.id")
    )
    owner_student_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("students.id")
    )
    kb_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("knowledge_bases.id")
    )
    name: Mapped[Optional[str]] = mapped_column(String(128))  # 对话显示名称
    ragflow_chat_id: Mapped[Optional[str]] = mapped_column(String(64))
    ragflow_session_id: Mapped[Optional[str]] = mapped_column(String(64))
    model_name: Mapped[Optional[str]] = mapped_column(String(64))
    top_n: Mapped[int] = mapped_column(Integer, default=5)
    similarity_threshold: Mapped[float] = mapped_column(Numeric(4, 3), default=0.2)
    show_citations: Mapped[bool] = mapped_column(Boolean, default=True)
    system_prompt: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )

    messages: Mapped[List["Message"]] = relationship(back_populates="conversation")
    owner_teacher: Mapped[Optional[Teacher]] = relationship()
    owner_student: Mapped[Optional[Student]] = relationship()
    kb: Mapped[Optional[KnowledgeBase]] = relationship()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_id", "conversation_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id"), nullable=False
    )
    sender_role: Mapped[SenderRole] = mapped_column(
        SAEnum(SenderRole), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# --- 搜索日志 ---
class SearchLog(Base):
    __tablename__ = "search_logs"
    __table_args__ = (
        Index("ix_search_logs_user_teacher_id", "user_teacher_id"),
        Index("ix_search_logs_user_student_id", "user_student_id"),
        Index("ix_search_logs_kb_id_created_at", "kb_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_teacher_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("teachers.id")
    )
    user_student_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("students.id")
    )
    kb_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("knowledge_bases.id"), nullable=False
    )
    query: Mapped[str] = mapped_column(String(512), nullable=False)
    result_count: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    user_teacher: Mapped[Optional[Teacher]] = relationship()
    user_student: Mapped[Optional[Student]] = relationship()
    kb: Mapped[KnowledgeBase] = relationship()


# --- RAGFlow 用户配置 ---
class RagflowSetting(Base):
    __tablename__ = "ragflow_settings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_teacher_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("teachers.id")
    )
    owner_student_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("students.id")
    )
    api_base: Mapped[str] = mapped_column(String(255), default="http://localhost:8080")
    api_key: Mapped[str] = mapped_column(String(255), nullable=False)
    default_model: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )

    owner_teacher: Mapped[Optional[Teacher]] = relationship()
    owner_student: Mapped[Optional[Student]] = relationship()
