from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from urllib.parse import urlparse
from datetime import datetime
from typing import Optional, List, Dict, Any
import uuid
import mimetypes
import hashlib

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


class SearchRequest(BaseModel):
    """搜索请求体。"""

    query: str
    role: str
    user_id: int
    kb_id: Optional[int] = None
    class_id: Optional[int] = None
    class_code: Optional[str] = None
    top_k: int = 5
    similarity_threshold: Optional[float] = None
    highlight: bool = True


class DocumentSearchRequest(BaseModel):
    """按文件名搜索请求体（用于文件管理）。"""

    role: str
    user_id: int
    kb_id: Optional[int] = None
    class_id: Optional[int] = None
    class_code: Optional[str] = None
    filename: str
    include_pending: bool = False
    include_rejected: bool = False


# ------------------------------
# 通用工具函数
# ------------------------------


def _verify_password(password: str, stored_hash: str) -> bool:
    """密码校验（学习版）。

    当前支持：
    1) 明文直接对比（仅用于开发/演示）
    2) SHA256(password) 与库中值对比
    """
    if stored_hash == password:
        return True
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


async def _ragflow_get_chunk(dataset_id: str, document_id: str, chunk_id: str) -> Dict[str, Any]:
    """从 RAGFlow 查询单个 chunk 内容，用于“点击查看案例”。"""
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = {"Authorization": f"Bearer {settings.ragflow_api_key}"}
    params = {"id": chunk_id, "page": 1, "page_size": 1}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{base_url}/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks",
            headers=headers,
            params=params,
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 查询 chunk 失败: HTTP {resp.status_code}")

    payload = resp.json()
    if payload.get("code") != 0:
        message = payload.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 查询 chunk 失败: {message}")

    data = payload.get("data")
    chunk = None
    if isinstance(data, dict):
        for key in ("chunks", "data", "items", "list"):
            value = data.get(key)
            if isinstance(value, list) and value:
                chunk = value[0]
                break
        if not chunk and "id" in data:
            chunk = data
    elif isinstance(data, list) and data:
        chunk = data[0]

    if not chunk:
        raise HTTPException(status_code=404, detail="未找到对应数据块")

    return chunk


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
        kb = (
            await session.execute(
                select(models.KnowledgeBase).where(models.KnowledgeBase.id == kb_id)
            )
        ).scalar_one_or_none()
        if kb:
            return kb

    if role == "student":
        student = (
            await session.execute(
                select(models.Student).where(models.Student.id == uploader_id)
            )
        ).scalar_one_or_none()
        if not student:
            raise HTTPException(status_code=404, detail="学生不存在")
        kb = (
            await session.execute(
                select(models.KnowledgeBase).where(
                    models.KnowledgeBase.class_id == student.class_id
                )
            )
        ).scalar_one_or_none()
        if kb:
            return kb

    if role in {"teacher", "admin"}:
        if class_id is None and class_code is not None:
            cls = (
                await session.execute(
                    select(models.Class).where(models.Class.class_code == class_code)
                )
            ).scalar_one_or_none()
            if cls:
                class_id = cls.id
        if class_id is not None:
            kb = (
                await session.execute(
                    select(models.KnowledgeBase).where(
                        models.KnowledgeBase.class_id == class_id
                    )
                )
            ).scalar_one_or_none()
            if kb:
                return kb

    raise HTTPException(status_code=400, detail="无法确定知识库，请传入 kb_id 或班级信息")


async def _resolve_kb_for_search(
    session: AsyncSession,
    role: str,
    user_id: int,
    kb_id: Optional[int],
    class_id: Optional[int],
    class_code: Optional[str],
) -> models.KnowledgeBase:
    """根据角色与班级信息定位知识库，并做访问权限校验。"""
    role = role.lower().strip()

    # 优先将班级编号解析为 class_id
    if class_id is None and class_code is not None:
        cls = (
            await session.execute(
                select(models.Class).where(models.Class.class_code == class_code)
            )
        ).scalar_one_or_none()
        if not cls:
            raise HTTPException(status_code=404, detail="班级不存在")
        class_id = cls.id

    # 学生：只能访问自己班级
    if role == "student":
        student = (
            await session.execute(
                select(models.Student).where(models.Student.id == user_id)
            )
        ).scalar_one_or_none()
        if not student or student.status != 1:
            raise HTTPException(status_code=403, detail="学生不存在或已停用")

        if kb_id is not None:
            kb = (
                await session.execute(
                    select(models.KnowledgeBase).where(models.KnowledgeBase.id == kb_id)
                )
            ).scalar_one_or_none()
            if not kb:
                raise HTTPException(status_code=404, detail="知识库不存在")
            if kb.class_id != student.class_id:
                raise HTTPException(status_code=403, detail="无权访问该知识库")
            return kb

        if class_id is not None and class_id != student.class_id:
            raise HTTPException(status_code=403, detail="无权访问该班级")

        kb = (
            await session.execute(
                select(models.KnowledgeBase).where(
                    models.KnowledgeBase.class_id == student.class_id
                )
            )
        ).scalar_one_or_none()
        if kb:
            return kb
        raise HTTPException(status_code=404, detail="班级未绑定知识库")

    # 教师：如果只有一个班级可自动选择；多个班级则必须选择
    if role == "teacher":
        teacher = (
            await session.execute(
                select(models.Teacher).where(models.Teacher.id == user_id)
            )
        ).scalar_one_or_none()
        if not teacher or teacher.status != 1:
            raise HTTPException(status_code=403, detail="教师不存在或已停用")

        if kb_id is not None:
            kb = (
                await session.execute(
                    select(models.KnowledgeBase).where(models.KnowledgeBase.id == kb_id)
                )
            ).scalar_one_or_none()
            if not kb:
                raise HTTPException(status_code=404, detail="知识库不存在")
            cls = (
                await session.execute(
                    select(models.Class).where(models.Class.id == kb.class_id)
                )
            ).scalar_one_or_none()
            if not cls or cls.teacher_id != teacher.id:
                raise HTTPException(status_code=403, detail="无权访问该知识库")
            return kb

        if class_id is None:
            classes = (
                await session.execute(
                    select(models.Class).where(models.Class.teacher_id == teacher.id)
                )
            ).scalars().all()
            if len(classes) == 1:
                class_id = classes[0].id
            else:
                raise HTTPException(status_code=400, detail="请先选择班级")

        cls = (
            await session.execute(select(models.Class).where(models.Class.id == class_id))
        ).scalar_one_or_none()
        if not cls:
            raise HTTPException(status_code=404, detail="班级不存在")
        if cls.teacher_id != teacher.id:
            raise HTTPException(status_code=403, detail="无权访问该班级")

        kb = (
            await session.execute(
                select(models.KnowledgeBase).where(
                    models.KnowledgeBase.class_id == class_id
                )
            )
        ).scalar_one_or_none()
        if kb:
            return kb
        raise HTTPException(status_code=404, detail="班级未绑定知识库")

    # 管理员：可访问任意班级/知识库
    if role == "admin":
        if kb_id is not None:
            kb = (
                await session.execute(
                    select(models.KnowledgeBase).where(models.KnowledgeBase.id == kb_id)
                )
            ).scalar_one_or_none()
            if kb:
                return kb
            raise HTTPException(status_code=404, detail="知识库不存在")

        if class_id is not None:
            kb = (
                await session.execute(
                    select(models.KnowledgeBase).where(
                        models.KnowledgeBase.class_id == class_id
                    )
                )
            ).scalar_one_or_none()
            if kb:
                return kb
            raise HTTPException(status_code=404, detail="班级未绑定知识库")

        raise HTTPException(status_code=400, detail="请指定班级或知识库")

    raise HTTPException(status_code=400, detail="role 参数不合法")


def _extract_ragflow_doc_id(item: Dict[str, Any]) -> Optional[str]:
    """从 RAGFlow chunk 中提取 document id。"""
    if not isinstance(item, dict):
        return None
    return (
        item.get("document_id")
        or item.get("doc_id")
        or (item.get("document") or {}).get("id")
    )


def _extract_document_name(item: Dict[str, Any], doc_info: Optional[Dict[str, Any]]) -> Optional[str]:
    """尽量从本地或 RAGFlow 结果中获取文档名称。"""
    if doc_info and doc_info.get("document_name"):
        return doc_info["document_name"]
    if not isinstance(item, dict):
        return None
    return item.get("document_name") or item.get("document_keyword") or item.get("name")


def _extract_case_locator(item: Dict[str, Any]) -> Dict[str, Any]:
    """提取“定位信息”，用于前端展示与点击跳转。"""
    if not isinstance(item, dict):
        return {}
    return {
        "location": item.get("location") or item.get("from_page"),
        "positions": item.get("positions"),
        "sheet": item.get("sheet"),
        "row": item.get("row"),
        "column": item.get("column"),
    }


def _format_search_chunks(
    raw_chunks: List[Dict[str, Any]],
    doc_map: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """格式化搜索结果，返回前端友好结构。"""
    results: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_chunks, start=1):
        if not isinstance(item, dict):
            continue
        ragflow_doc_id = _extract_ragflow_doc_id(item)
        doc_info = doc_map.get(ragflow_doc_id) if ragflow_doc_id else None
        document_name = _extract_document_name(item, doc_info)
        locator = _extract_case_locator(item)

        content = item.get("content") or ""
        highlight = item.get("highlight") or content

        results.append(
            {
                "rank": idx,
                "content": content,
                "highlight": highlight,
                "score": item.get("score") or item.get("similarity") or item.get("distance"),
                "chunk_id": item.get("id") or item.get("chunk_id"),
                "location": item.get("location") or item.get("from_page"),
                "ragflow_document_id": ragflow_doc_id,
                "document_name": document_name,
                "case_locator": locator,
                "preview": {
                    "ragflow_document_id": ragflow_doc_id,
                    "chunk_id": item.get("id") or item.get("chunk_id"),
                    "kb_id": doc_info.get("kb_id") if doc_info else None,
                    "class_id": doc_info.get("class_id") if doc_info else None,
                    "class_code": doc_info.get("class_code") if doc_info else None,
                },
                # 本地关联信息（用于前端定位/跳转）
                "document_id": doc_info.get("document_id") if doc_info else None,
                "kb_id": doc_info.get("kb_id") if doc_info else None,
                "class_id": doc_info.get("class_id") if doc_info else None,
                "class_code": doc_info.get("class_code") if doc_info else None,
                "class_name": doc_info.get("class_name") if doc_info else None,
            }
        )
    return results


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


@app.get("/teachers/{teacher_id}/classes")
async def list_teacher_classes(
    teacher_id: int,
    session: AsyncSession = Depends(get_session),
):
    """列出教师所授班级，用于“先选班级再搜索”。"""
    teacher = (
        await session.execute(
            select(models.Teacher).where(models.Teacher.id == teacher_id)
        )
    ).scalar_one_or_none()
    if not teacher or teacher.status != 1:
        raise HTTPException(status_code=404, detail="教师不存在或已停用")

    rows = (
        await session.execute(
            select(models.Class, models.KnowledgeBase)
            .outerjoin(models.KnowledgeBase, models.KnowledgeBase.class_id == models.Class.id)
            .where(models.Class.teacher_id == teacher_id)
            .order_by(models.Class.class_code)
        )
    ).all()

    return [
        {
            "class_id": cls.id,
            "class_code": cls.class_code,
            "class_name": cls.class_name,
            "teacher_id": cls.teacher_id,
            "kb_id": kb.id if kb else None,
            "ragflow_dataset_id": kb.ragflow_dataset_id if kb else None,
        }
        for cls, kb in rows
    ]


@app.post("/classes")
async def create_class(
    payload: CreateClassRequest,
    session: AsyncSession = Depends(get_session),
):
    """创建班级并同步创建 RAGFlow 数据集。"""
    # 检查教师是否存在
    teacher = (
        await session.execute(
            select(models.Teacher).where(models.Teacher.teacher_no == payload.teacher_no)
        )
    ).scalar_one_or_none()
    if not teacher:
        raise HTTPException(status_code=404, detail="教师不存在")

    # 班级编号是否已存在
    exists = (
        await session.execute(
            select(models.Class).where(models.Class.class_code == payload.class_code)
        )
    ).scalar_one_or_none()
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

    status = (
        models.DocumentStatus.pending
        if role == "student"
        else models.DocumentStatus.approved
    )
    target_bucket = pending_bucket if status == models.DocumentStatus.pending else kb_bucket

    ext = ""
    if file.filename:
        ext = file.filename.split(".")[-1]
        ext = f".{ext}" if ext else ""
    object_name = f"{kb.id}/{datetime.utcnow().strftime('%Y%m%d')}/{uuid.uuid4().hex}{ext}"

    content_type = (
        file.content_type
        or mimetypes.guess_type(file.filename or "")[0]
        or "application/octet-stream"
    )
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


@app.post("/documents/search")
async def search_documents_by_filename(
    payload: DocumentSearchRequest,
    session: AsyncSession = Depends(get_session),
):
    """按文件名搜索（仅返回文件列表，不做内容检索）。"""
    role = payload.role.lower().strip()
    if role not in {"student", "teacher", "admin"}:
        raise HTTPException(status_code=400, detail="role 参数不合法")

    kb = await _resolve_kb_for_search(
        session,
        role,
        payload.user_id,
        payload.kb_id,
        payload.class_id,
        payload.class_code,
    )

    # 默认仅返回知识库中可用文件（通过/已嵌入）
    allowed_statuses = {models.DocumentStatus.approved, models.DocumentStatus.embedded}

    # 管理员或教师可选择扩展范围
    if payload.include_pending and role in {"teacher", "admin"}:
        allowed_statuses.add(models.DocumentStatus.pending)
    if payload.include_rejected and role == "admin":
        allowed_statuses.add(models.DocumentStatus.rejected)

    stmt = (
        select(models.Document, models.Class)
        .join(models.KnowledgeBase, models.Document.kb_id == models.KnowledgeBase.id)
        .join(models.Class, models.KnowledgeBase.class_id == models.Class.id)
        .where(models.Document.kb_id == kb.id)
        .where(models.Document.status.in_(allowed_statuses))
        .order_by(models.Document.uploaded_at.desc())
    )

    keyword = payload.filename.strip()
    if keyword:
        stmt = stmt.where(models.Document.original_name.like(f"%{keyword}%"))

    rows = (await session.execute(stmt)).all()

    return [
        {
            "document_id": doc.id,
            "document_name": doc.original_name,
            "status": doc.status.value,
            "uploaded_at": doc.uploaded_at,
            "ragflow_document_id": doc.ragflow_document_id,
            "kb_id": doc.kb_id,
            "class_id": cls.id,
            "class_code": cls.class_code,
            "class_name": cls.class_name,
        }
        for doc, cls in rows
    ]


@app.get("/audits/pending")
async def list_pending_audits(
    class_id: Optional[int] = None,
    class_code: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """查询待审核文件列表。"""
    stmt = select(models.Document).where(models.Document.status == models.DocumentStatus.pending)

    if class_id is None and class_code is not None:
        cls = (
            await session.execute(
                select(models.Class).where(models.Class.class_code == class_code)
            )
        ).scalar_one_or_none()
        if cls:
            class_id = cls.id

    if class_id is not None:
        stmt = stmt.where(
            models.Document.kb_id.in_(
                select(models.KnowledgeBase.id).where(models.KnowledgeBase.class_id == class_id)
            )
        )

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
    admin = (
        await session.execute(
            select(models.Admin).where(models.Admin.id == payload.reviewer_admin_id)
        )
    ).scalar_one_or_none()
    if not admin or admin.status != 1:
        raise HTTPException(status_code=403, detail="管理员不存在或已停用")

    doc = (
        await session.execute(
            select(models.Document).where(models.Document.id == document_id)
        )
    ).scalar_one_or_none()
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
        # 通过：从待审核桶移动到知识库桶
        try:
            source = CopySource(pending_bucket, doc.storage_path)
            client.copy_object(kb_bucket, doc.storage_path, source)
            client.remove_object(pending_bucket, doc.storage_path)
        except S3Error as exc:
            raise HTTPException(status_code=500, detail=f"MinIO 移动失败: {exc.code}")
        doc.status = models.DocumentStatus.approved
    else:
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
    teacher = (
        await session.execute(
            select(models.Teacher).where(models.Teacher.id == payload.teacher_id)
        )
    ).scalar_one_or_none()
    if not teacher or teacher.status != 1:
        raise HTTPException(status_code=403, detail="教师不存在或已停用")

    doc = (
        await session.execute(
            select(models.Document).where(models.Document.id == document_id)
        )
    ).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.status != models.DocumentStatus.approved:
        raise HTTPException(status_code=400, detail="文档未通过审核")

    kb = (
        await session.execute(
            select(models.KnowledgeBase).where(models.KnowledgeBase.id == doc.kb_id)
        )
    ).scalar_one_or_none()
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

        # 若已存在 ragflow_document_id，直接复用
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


# ------------------------------
# 搜索接口（RAGFlow retrieval）
# ------------------------------


@app.post("/search")
async def search_cases(
    payload: SearchRequest,
    session: AsyncSession = Depends(get_session),
):
    """基于 RAGFlow 的检索接口。"""
    role = payload.role.lower().strip()
    if role not in {"student", "teacher", "admin"}:
        raise HTTPException(status_code=400, detail="role 参数不合法")

    kb = await _resolve_kb_for_search(
        session,
        role,
        payload.user_id,
        payload.kb_id,
        payload.class_id,
        payload.class_code,
    )

    if not kb.ragflow_dataset_id:
        raise HTTPException(status_code=400, detail="知识库未绑定 RAGFlow dataset")

    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = {"Authorization": f"Bearer {settings.ragflow_api_key}"}
    body = {
        "question": payload.query,
        "dataset_ids": [kb.ragflow_dataset_id],
        "top_k": payload.top_k,
        "highlight": payload.highlight,
    }
    if payload.similarity_threshold is not None:
        body["similarity_threshold"] = payload.similarity_threshold

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{base_url}/api/v1/retrieval", json=body, headers=headers)

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 检索失败: HTTP {resp.status_code}")

    data = resp.json()
    if data.get("code") != 0:
        message = data.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 检索失败: {message}")

    raw_chunks = data.get("data", {}).get("chunks", [])

    # 关联本地文档信息，方便前端定位（可点击跳转）
    ragflow_ids = list({rid for rid in map(_extract_ragflow_doc_id, raw_chunks) if rid})
    doc_map: Dict[str, Dict[str, Any]] = {}
    if ragflow_ids:
        rows = (
            await session.execute(
                select(models.Document, models.KnowledgeBase, models.Class)
                .join(models.KnowledgeBase, models.Document.kb_id == models.KnowledgeBase.id)
                .join(models.Class, models.KnowledgeBase.class_id == models.Class.id)
                .where(models.Document.ragflow_document_id.in_(ragflow_ids))
            )
        ).all()
        for doc, kb_row, cls in rows:
            doc_map[doc.ragflow_document_id] = {
                "document_id": doc.id,
                "document_name": doc.original_name,
                "kb_id": kb_row.id,
                "class_id": cls.id,
                "class_code": cls.class_code,
                "class_name": cls.class_name,
            }

    formatted = _format_search_chunks(raw_chunks, doc_map)

    # 写入搜索日志
    log = models.SearchLog(
        user_teacher_id=payload.user_id if role == "teacher" else None,
        user_student_id=payload.user_id if role == "student" else None,
        kb_id=kb.id,
        query=payload.query,
        result_count=len(formatted),
        created_at=datetime.utcnow(),
    )
    session.add(log)
    await session.commit()

    return {
        "kb_id": kb.id,
        "ragflow_dataset_id": kb.ragflow_dataset_id,
        "result_count": len(formatted),
        "chunks": formatted,
    }


# ------------------------------
# 案例预览（基于 RAGFlow chunk 查询）
# ------------------------------


@app.get("/cases/preview")
async def preview_case(
    role: str,
    user_id: int,
    ragflow_document_id: str,
    chunk_id: str,
    kb_id: Optional[int] = None,
    class_id: Optional[int] = None,
    class_code: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """根据 chunk_id 获取案例内容，供前端点击查看。"""
    role = role.lower().strip()
    if role not in {"student", "teacher", "admin"}:
        raise HTTPException(status_code=400, detail="role 参数不合法")

    kb = await _resolve_kb_for_search(
        session,
        role,
        user_id,
        kb_id,
        class_id,
        class_code,
    )

    doc = (
        await session.execute(
            select(models.Document).where(
                models.Document.kb_id == kb.id,
                models.Document.ragflow_document_id == ragflow_document_id,
            )
        )
    ).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="未找到对应文档")

    chunk = await _ragflow_get_chunk(kb.ragflow_dataset_id, ragflow_document_id, chunk_id)

    return {
        "kb_id": kb.id,
        "class_id": kb.class_id,
        "document_id": doc.id,
        "document_name": doc.original_name,
        "ragflow_document_id": ragflow_document_id,
        "chunk_id": chunk_id,
        "content": chunk.get("content"),
        "highlight": chunk.get("highlight") or chunk.get("content"),
        "location": chunk.get("location") or chunk.get("from_page"),
        "positions": chunk.get("positions"),
        "raw_chunk": chunk,
    }
