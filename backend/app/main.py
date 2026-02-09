from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from urllib.parse import urlparse
from datetime import datetime
from typing import Optional, List
import uuid
import mimetypes

import httpx
from minio import Minio
from minio.error import S3Error
from minio.commonconfig import CopySource

from app.config import get_settings
from app.db import get_session
from app import models

settings = get_settings()
app = FastAPI(title="CaseHub API", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/config")
async def read_config():
    return {
        "db_url": settings.db_url,
        "ragflow_base_url": str(settings.ragflow_base_url),
        "minio_endpoint": str(settings.minio_endpoint),
    }


class LoginRequest(BaseModel):
    """通用登录请求体。"""

    account: str
    password: str


class CreateClassRequest(BaseModel):
    """创建班级请求体（同步创建 RAGFlow 数据集）。"""

    class_code: str
    class_name: str
    teacher_no: str
    embedding_model: str
    description: Optional[str] = None
    chunk_method: str = "table"
    permission: str = "me"


class AuditDecisionRequest(BaseModel):
    """审核请求体。"""

    reviewer_admin_id: int
    decision: str  # approved / rejected
    reason: Optional[str] = None


class EmbeddingRunRequest(BaseModel):
    """触发嵌入任务请求体。"""

    teacher_id: int
    chunk_method: Optional[str] = None


def _verify_password(password: str, stored_hash: str) -> bool:
    """密码校验（学习版）。

    当前支持：
    1) 明文直接对比（仅用于开发/演示）
    2) SHA256(password) 与库中值对比
    """
    if stored_hash == password:
        return True
    import hashlib

    sha256 = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return stored_hash == sha256


async def _login_admin(payload: LoginRequest, session: AsyncSession):
    stmt = select(models.Admin).where(
        or_(
            models.Admin.admin_no == payload.account,
            models.Admin.username == payload.account,
        )
    )
    admin = (await session.execute(stmt)).scalar_one_or_none()
    if not admin or not _verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if admin.status != 1:
        raise HTTPException(status_code=403, detail="账号已停用")
    return {
        "role": "admin",
        "id": admin.id,
        "name": admin.name,
        "admin_no": admin.admin_no,
    }


async def _login_teacher(payload: LoginRequest, session: AsyncSession):
    stmt = select(models.Teacher).where(models.Teacher.teacher_no == payload.account)
    teacher = (await session.execute(stmt)).scalar_one_or_none()
    if not teacher or not _verify_password(payload.password, teacher.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if teacher.status != 1:
        raise HTTPException(status_code=403, detail="账号已停用")
    return {
        "role": "teacher",
        "id": teacher.id,
        "name": teacher.name,
        "teacher_no": teacher.teacher_no,
    }


async def _login_student(payload: LoginRequest, session: AsyncSession):
    stmt = select(models.Student).where(models.Student.student_no == payload.account)
    student = (await session.execute(stmt)).scalar_one_or_none()
    if not student or not _verify_password(payload.password, student.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if student.status != 1:
        raise HTTPException(status_code=403, detail="账号已停用")
    return {
        "role": "student",
        "id": student.id,
        "name": student.name,
        "student_no": student.student_no,
    }


def _get_minio_client() -> Minio:
    """创建 MinIO 客户端。"""
    endpoint = str(settings.minio_endpoint)
    parsed = urlparse(endpoint)
    if parsed.scheme:
        host = parsed.netloc
        secure = parsed.scheme == "https"
    else:
        host = endpoint
        secure = False

    return Minio(
        host,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=secure,
    )


def _ensure_bucket(client: Minio, bucket: str) -> None:
    """确保桶存在。"""
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def _read_minio_object(client: Minio, bucket: str, object_name: str) -> bytes:
    """从 MinIO 读取对象内容。"""
    try:
        obj = client.get_object(bucket, object_name)
        try:
            return obj.read()
        finally:
            obj.close()
            obj.release_conn()
    except S3Error as exc:
        raise HTTPException(status_code=500, detail=f"MinIO 读取失败: {exc.code}")


async def _create_ragflow_dataset(payload: CreateClassRequest) -> str:
    """调用 RAGFlow 创建数据集，返回 dataset_id。"""
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = {"Authorization": f"Bearer {settings.ragflow_api_key}"}
    body = {
        "name": payload.class_code,
        "embedding_model": payload.embedding_model,
        "chunk_method": payload.chunk_method,
        "parser_config": {},
        "permission": payload.permission,
    }
    if payload.description:
        body["description"] = payload.description

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{base_url}/api/v1/datasets", json=body, headers=headers)

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 创建失败: HTTP {resp.status_code}")

    data = resp.json()
    if data.get("code") != 0:
        message = data.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 创建失败: {message}")

    dataset_id = None
    if isinstance(data.get("data"), dict):
        dataset_id = data["data"].get("id") or data["data"].get("dataset_id")

    if not dataset_id:
        raise HTTPException(status_code=502, detail="RAGFlow 返回缺少 dataset_id")

    return dataset_id


async def _ragflow_upload_document(dataset_id: str, filename: str, data: bytes, content_type: str) -> str:
    """上传文件到 RAGFlow，返回 ragflow_document_id。"""
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = {"Authorization": f"Bearer {settings.ragflow_api_key}"}
    files = {"file": (filename, data, content_type)}

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/api/v1/datasets/{dataset_id}/documents",
            headers=headers,
            files=files,
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 上传失败: HTTP {resp.status_code}")

    payload = resp.json()
    if payload.get("code") != 0:
        message = payload.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 上传失败: {message}")

    data_list = payload.get("data", [])
    if not data_list:
        raise HTTPException(status_code=502, detail="RAGFlow 上传返回空列表")

    ragflow_doc_id = data_list[0].get("id")
    if not ragflow_doc_id:
        raise HTTPException(status_code=502, detail="RAGFlow 返回缺少 document id")

    return ragflow_doc_id


async def _ragflow_parse_documents(dataset_id: str, document_ids: List[str]) -> None:
    """调用 RAGFlow 进行解析/嵌入。"""
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = {"Authorization": f"Bearer {settings.ragflow_api_key}"}
    body = {"document_ids": document_ids}

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/api/v1/datasets/{dataset_id}/chunks",
            headers=headers,
            json=body,
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 解析失败: HTTP {resp.status_code}")

    payload = resp.json()
    if payload.get("code") != 0:
        message = payload.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 解析失败: {message}")


async def _resolve_kb(
    session: AsyncSession,
    role: str,
    uploader_id: int,
    class_id: Optional[int],
    class_code: Optional[str],
    kb_id: Optional[int],
) -> models.KnowledgeBase:
    """根据角色和传参定位知识库。"""
    if kb_id:
        kb = (await session.execute(
            select(models.KnowledgeBase).where(models.KnowledgeBase.id == kb_id)
        )).scalar_one_or_none()
        if kb:
            return kb

    if role == "student":
        student = (await session.execute(
            select(models.Student).where(models.Student.id == uploader_id)
        )).scalar_one_or_none()
        if not student:
            raise HTTPException(status_code=404, detail="学生不存在")
        kb = (await session.execute(
            select(models.KnowledgeBase).where(models.KnowledgeBase.class_id == student.class_id)
        )).scalar_one_or_none()
        if kb:
            return kb

    if role in {"teacher", "admin"}:
        if class_id is None and class_code is not None:
            cls = (await session.execute(
                select(models.Class).where(models.Class.class_code == class_code)
            )).scalar_one_or_none()
            if cls:
                class_id = cls.id
        if class_id is not None:
            kb = (await session.execute(
                select(models.KnowledgeBase).where(models.KnowledgeBase.class_id == class_id)
            )).scalar_one_or_none()
            if kb:
                return kb

    raise HTTPException(status_code=400, detail="无法确定知识库，请传入 kb_id 或班级信息")


# ------------------------------
# 登录模块
# ------------------------------

@app.post("/auth/admin/login")
async def admin_login(payload: LoginRequest, session: AsyncSession = Depends(get_session)):
    return await _login_admin(payload, session)


@app.post("/auth/teacher/login")
async def teacher_login(payload: LoginRequest, session: AsyncSession = Depends(get_session)):
    return await _login_teacher(payload, session)


@app.post("/auth/student/login")
async def student_login(payload: LoginRequest, session: AsyncSession = Depends(get_session)):
    return await _login_student(payload, session)


# ------------------------------
# 班级创建（同步创建 RAGFlow 数据集）
# ------------------------------

@app.post("/classes")
async def create_class(
    payload: CreateClassRequest,
    session: AsyncSession = Depends(get_session),
):
    """创建班级并同时创建 RAGFlow 数据集。"""
    # 检查教师是否存在
    teacher = (await session.execute(
        select(models.Teacher).where(models.Teacher.teacher_no == payload.teacher_no)
    )).scalar_one_or_none()
    if not teacher:
        raise HTTPException(status_code=404, detail="教师不存在")

    # 班级编号是否已存在
    exists = (await session.execute(
        select(models.Class).where(models.Class.class_code == payload.class_code)
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="班级编号已存在")

    # 先调用 RAGFlow 创建数据集
    dataset_id = await _create_ragflow_dataset(payload)

    # 本地创建班级
    new_class = models.Class(
        class_code=payload.class_code,
        class_name=payload.class_name,
        teacher_id=teacher.id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(new_class)
    await session.flush()

    # 创建对应知识库
    kb = models.KnowledgeBase(
        class_id=new_class.id,
        name=f"{payload.class_name}知识库",
        description=payload.description,
        ragflow_dataset_id=dataset_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(kb)
    await session.commit()
    await session.refresh(new_class)
    await session.refresh(kb)

    return {
        "class_id": new_class.id,
        "class_code": new_class.class_code,
        "class_name": new_class.class_name,
        "teacher_id": new_class.teacher_id,
        "kb_id": kb.id,
        "ragflow_dataset_id": kb.ragflow_dataset_id,
    }


# ------------------------------
# 文件上传与审核
# ------------------------------

@app.post("/documents/upload")
async def upload_document(
    role: str = Form(...),
    uploader_id: int = Form(...),
    file: UploadFile = File(...),
    class_id: Optional[int] = Form(None),
    class_code: Optional[str] = Form(None),
    kb_id: Optional[int] = Form(None),
    session: AsyncSession = Depends(get_session),
):
    """上传文件并记录数据库。

    - 学生上传：状态为 pending，进入待审核桶
    - 教师/管理员上传：状态为 approved，进入知识库桶
    """
    role = role.lower().strip()
    if role not in {"student", "teacher", "admin"}:
        raise HTTPException(status_code=400, detail="role 参数不合法")

    kb = await _resolve_kb(session, role, uploader_id, class_id, class_code, kb_id)

    client = _get_minio_client()
    pending_bucket = settings.minio_bucket_pending
    kb_bucket = settings.minio_bucket_kb
    _ensure_bucket(client, pending_bucket)
    _ensure_bucket(client, kb_bucket)

    status = models.DocumentStatus.pending if role == "student" else models.DocumentStatus.approved
    target_bucket = pending_bucket if status == models.DocumentStatus.pending else kb_bucket

    ext = ""
    if file.filename:
        ext = file.filename.split(".")[-1]
        ext = f".{ext}" if ext else ""
    object_name = f"{kb.id}/{datetime.utcnow().strftime('%Y%m%d')}/{uuid.uuid4().hex}{ext}"

    content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    try:
        client.put_object(
            target_bucket,
            object_name,
            file.file,
            length=-1,
            part_size=10 * 1024 * 1024,
            content_type=content_type,
        )
    except S3Error as exc:
        raise HTTPException(status_code=500, detail=f"MinIO 上传失败: {exc.code}")

    doc = models.Document(
        kb_id=kb.id,
        filename=object_name,
        original_name=file.filename or "",
        uploader_student_id=uploader_id if role == "student" else None,
        uploader_teacher_id=uploader_id if role == "teacher" else None,
        uploader_admin_id=uploader_id if role == "admin" else None,
        size_bytes=None,
        mime_type=content_type,
        ragflow_document_id=None,
        status=status,
        storage_path=object_name,
        uploaded_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    return {
        "id": doc.id,
        "kb_id": doc.kb_id,
        "status": doc.status.value,
        "filename": doc.original_name,
    }


@app.get("/audits/pending")
async def list_pending_audits(
    class_id: Optional[int] = None,
    class_code: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """查询待审核文件列表。"""
    stmt = select(models.Document).where(models.Document.status == models.DocumentStatus.pending)

    if class_id is None and class_code is not None:
        cls = (await session.execute(
            select(models.Class).where(models.Class.class_code == class_code)
        )).scalar_one_or_none()
        if cls:
            class_id = cls.id

    if class_id is not None:
        stmt = stmt.where(models.Document.kb_id.in_(
            select(models.KnowledgeBase.id).where(models.KnowledgeBase.class_id == class_id)
        ))

    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": d.id,
            "kb_id": d.kb_id,
            "original_name": d.original_name,
            "uploader_student_id": d.uploader_student_id,
            "uploader_teacher_id": d.uploader_teacher_id,
            "uploader_admin_id": d.uploader_admin_id,
            "uploaded_at": d.uploaded_at,
        }
        for d in rows
    ]


@app.post("/audits/{document_id}/decision")
async def audit_document(
    document_id: int,
    payload: AuditDecisionRequest,
    session: AsyncSession = Depends(get_session),
):
    """管理员审核文件（通过/拒绝）。"""
    admin = (await session.execute(
        select(models.Admin).where(models.Admin.id == payload.reviewer_admin_id)
    )).scalar_one_or_none()
    if not admin or admin.status != 1:
        raise HTTPException(status_code=403, detail="管理员不存在或已停用")

    doc = (await session.execute(
        select(models.Document).where(models.Document.id == document_id)
    )).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    decision = payload.decision.lower().strip()
    if decision not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="decision 必须为 approved 或 rejected")

    client = _get_minio_client()
    pending_bucket = settings.minio_bucket_pending
    kb_bucket = settings.minio_bucket_kb
    _ensure_bucket(client, pending_bucket)
    _ensure_bucket(client, kb_bucket)

    if decision == "approved":
        # 通过：将文件从待审核桶移动到知识库桶
        try:
            source = CopySource(pending_bucket, doc.storage_path)
            client.copy_object(kb_bucket, doc.storage_path, source)
            client.remove_object(pending_bucket, doc.storage_path)
        except S3Error as exc:
            raise HTTPException(status_code=500, detail=f"MinIO 移动失败: {exc.code}")
        doc.status = models.DocumentStatus.approved
    else:
        # 拒绝：仅更新状态
        doc.status = models.DocumentStatus.rejected

    doc.updated_at = datetime.utcnow()

    audit = models.DocumentAudit(
        document_id=doc.id,
        reviewer_admin_id=admin.id,
        decision=models.AuditDecision.approved if decision == "approved" else models.AuditDecision.rejected,
        reason=payload.reason,
        decided_at=datetime.utcnow(),
    )
    session.add(audit)
    await session.commit()

    return {"id": doc.id, "status": doc.status.value, "decision": decision}


# ------------------------------
# 嵌入管理（RAGFlow 文件上传 + 解析）
# ------------------------------

@app.post("/embeddings/{document_id}/run")
async def run_embedding(
    document_id: int,
    payload: EmbeddingRunRequest,
    session: AsyncSession = Depends(get_session),
):
    """将已审核文件上传到 RAGFlow 并解析。"""
    teacher = (await session.execute(
        select(models.Teacher).where(models.Teacher.id == payload.teacher_id)
    )).scalar_one_or_none()
    if not teacher or teacher.status != 1:
        raise HTTPException(status_code=403, detail="教师不存在或已停用")

    doc = (await session.execute(
        select(models.Document).where(models.Document.id == document_id)
    )).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.status != models.DocumentStatus.approved:
        raise HTTPException(status_code=400, detail="文档未通过审核")

    kb = (await session.execute(
        select(models.KnowledgeBase).where(models.KnowledgeBase.id == doc.kb_id)
    )).scalar_one_or_none()
    if not kb or not kb.ragflow_dataset_id:
        raise HTTPException(status_code=400, detail="知识库未绑定 RAGFlow dataset")

    task = models.EmbeddingTask(
        document_id=doc.id,
        triggered_by_teacher_id=teacher.id,
        chunk_method=payload.chunk_method or "table",
        status=models.EmbeddingTaskStatus.running,
        started_at=datetime.utcnow(),
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    try:
        client = _get_minio_client()
        kb_bucket = settings.minio_bucket_kb
        _ensure_bucket(client, kb_bucket)
        data = _read_minio_object(client, kb_bucket, doc.storage_path)
        filename = doc.original_name or doc.filename
        content_type = doc.mime_type or "application/octet-stream"

        # 若已有 ragflow_document_id，直接复用
        ragflow_doc_id = doc.ragflow_document_id
        if not ragflow_doc_id:
            ragflow_doc_id = await _ragflow_upload_document(
                kb.ragflow_dataset_id,
                filename,
                data,
                content_type,
            )
            doc.ragflow_document_id = ragflow_doc_id

        await _ragflow_parse_documents(kb.ragflow_dataset_id, [ragflow_doc_id])

        doc.status = models.DocumentStatus.embedded
        task.status = models.EmbeddingTaskStatus.success
        task.finished_at = datetime.utcnow()
        await session.commit()
    except HTTPException as exc:
        task.status = models.EmbeddingTaskStatus.failed
        task.message = str(exc.detail)
        task.finished_at = datetime.utcnow()
        await session.commit()
        raise
    except Exception as exc:
        task.status = models.EmbeddingTaskStatus.failed
        task.message = str(exc)
        task.finished_at = datetime.utcnow()
        await session.commit()
        raise HTTPException(status_code=500, detail="嵌入任务失败")

    return {
        "task_id": task.id,
        "document_id": doc.id,
        "ragflow_document_id": doc.ragflow_document_id,
        "status": task.status.value,
    }
