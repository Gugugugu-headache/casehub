from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    BigInteger,
    String,
    Text,
    Boolean,
    ForeignKey,
    UniqueConstraint,
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class DocumentStatus(str, enum.Enum):
    """文档在业务流转中的状态枚举。"""

    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    embedded = "embedded"


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


class Document(Base):
    __tablename__ = "documents"

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
