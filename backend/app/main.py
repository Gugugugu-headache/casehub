from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, or_, func, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from urllib.parse import urlparse, quote
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import uuid
import mimetypes
import hashlib
import io

import httpx
from minio import Minio
from minio.error import S3Error
from minio.commonconfig import CopySource

from app.config import get_settings
from app.db import get_session
from app import models

settings = get_settings()
app = FastAPI(title="CaseHub API", version="0.1.0")

# 允许前端跨域访问（本地开发/容器访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8081",
        "http://127.0.0.1:8081",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


class StudentRegisterRequest(BaseModel):
    """学生注册请求体。"""

    student_no: str
    password: str
    class_code: str
    name: Optional[str] = None


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


class RenameDocumentRequest(BaseModel):
    """重命名文件请求体。"""

    role: str
    user_id: int
    new_name: str
    sync_ragflow: bool = True


class DeleteDocumentRequest(BaseModel):
    """删除文件请求体。"""

    role: str
    user_id: int
    sync_ragflow: bool = True
    remove_minio: bool = True


class CreateConversationRequest(BaseModel):
    """创建对话请求体。"""

    role: str
    user_id: int
    kb_id: Optional[int] = None
    class_id: Optional[int] = None
    class_code: Optional[str] = None
    name: Optional[str] = None
    model_name: Optional[str] = None
    top_n: int = 5
    similarity_threshold: float = 0.2
    show_citations: bool = True
    system_prompt: Optional[str] = None


class SendMessageRequest(BaseModel):
    """发送对话消息请求体。"""

    role: str
    user_id: int
    content: str
    stream: bool = False
    metadata_condition: Optional[dict] = None


class UpdateConversationSettingsRequest(BaseModel):
    """更新对话设置请求体。"""

    role: str
    user_id: int
    model_name: Optional[str] = None
    system_prompt: Optional[str] = None
    top_n: Optional[int] = None
    similarity_threshold: Optional[float] = None
    show_citations: Optional[bool] = None
    sync_ragflow: bool = True


class RenameConversationRequest(BaseModel):
    """重命名对话请求体。"""

    role: str
    user_id: int
    new_name: str
    sync_ragflow: bool = True
    sync_session: bool = True


class ClearConversationRequest(BaseModel):
    """清空对话请求体。"""

    role: str
    user_id: int
    sync_ragflow: bool = True
    reset_session: bool = True


class DeleteConversationRequest(BaseModel):
    """删除对话请求体。"""

    role: str
    user_id: int
    sync_ragflow: bool = True


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


def _hash_password(password: str) -> str:
    """对密码做 SHA256 摘要（注册时使用）。"""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _normalize_system_prompt(system_prompt: str) -> str:
    """确保系统提示词包含 {knowledge} 占位符。"""
    text = (system_prompt or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="system_prompt 不能为空")
    if "{knowledge}" not in text:
        text = f"{text}\n\n以下是知识库内容：\n{{knowledge}}"
    return text


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


def _ragflow_headers() -> Dict[str, str]:
    """构造 RAGFlow 请求头（可选指定 Host）。"""
    headers = {"Authorization": f"Bearer {settings.ragflow_api_key}"}
    if settings.ragflow_host_header:
        headers["Host"] = settings.ragflow_host_header
    return headers


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


def _safe_remove_minio_object(client: Minio, bucket: str, object_name: str) -> None:
    """安全删除 MinIO 对象（不存在则忽略）。"""
    try:
        client.remove_object(bucket, object_name)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchObject"}:
            return
        raise HTTPException(status_code=500, detail=f"MinIO 删除失败: {exc.code}")


async def _check_document_permission(
    session: AsyncSession,
    role: str,
    user_id: int,
    cls: models.Class,
) -> None:
    """校验单个文档的访问权限。"""
    role = role.lower().strip()

    if role == "student":
        student = (
            await session.execute(
                select(models.Student).where(models.Student.id == user_id)
            )
        ).scalar_one_or_none()
        if not student or student.status != 1:
            raise HTTPException(status_code=403, detail="学生不存在或已停用")
        if student.class_id != cls.id:
            raise HTTPException(status_code=403, detail="无权访问该班级文件")
        return

    if role == "teacher":
        teacher = (
            await session.execute(
                select(models.Teacher).where(models.Teacher.id == user_id)
            )
        ).scalar_one_or_none()
        if not teacher or teacher.status != 1:
            raise HTTPException(status_code=403, detail="教师不存在或已停用")
        if cls.teacher_id != teacher.id:
            raise HTTPException(status_code=403, detail="无权访问该班级文件")
        return

    if role == "admin":
        admin = (
            await session.execute(
                select(models.Admin).where(models.Admin.id == user_id)
            )
        ).scalar_one_or_none()
        if not admin or admin.status != 1:
            raise HTTPException(status_code=403, detail="管理员不存在或已停用")
        return

    raise HTTPException(status_code=400, detail="role 参数不合法")


async def _get_conversation_for_owner(
    session: AsyncSession,
    conversation_id: int,
    role: str,
    user_id: int,
) -> models.Conversation:
    """获取对话并校验归属权限。"""
    role = role.lower().strip()
    if role not in {"teacher", "student"}:
        raise HTTPException(status_code=403, detail="仅教师或学生可操作对话")

    conv = (
        await session.execute(
            select(models.Conversation).where(models.Conversation.id == conversation_id)
        )
    ).scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")

    if role == "teacher" and conv.owner_teacher_id != user_id:
        raise HTTPException(status_code=403, detail="无权操作该对话")
    if role == "student" and conv.owner_student_id != user_id:
        raise HTTPException(status_code=403, detail="无权操作该对话")

    return conv


async def _create_ragflow_dataset(payload: CreateClassRequest) -> str:
    """调用 RAGFlow 创建数据集，返回 dataset_id。"""
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = _ragflow_headers()
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
    headers = _ragflow_headers()
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
    headers = _ragflow_headers()
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


async def _ragflow_update_document_name(dataset_id: str, document_id: str, new_name: str) -> None:
    """同步更新 RAGFlow 文档名称。"""
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = _ragflow_headers()
    body = {"name": new_name}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"{base_url}/api/v1/datasets/{dataset_id}/documents/{document_id}",
            headers=headers,
            json=body,
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 重命名失败: HTTP {resp.status_code}")

    payload = resp.json()
    if payload.get("code") != 0:
        message = payload.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 重命名失败: {message}")


async def _ragflow_delete_documents(dataset_id: str, document_ids: List[str]) -> None:
    """同步删除 RAGFlow 文档。"""
    if not document_ids:
        return
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = _ragflow_headers()
    body = {"ids": document_ids}

    async with httpx.AsyncClient(timeout=30) as client:
        # httpx 旧版本 delete 不支持 json 参数，使用 request 兼容
        resp = await client.request(
            "DELETE",
            f"{base_url}/api/v1/datasets/{dataset_id}/documents",
            headers=headers,
            json=body,
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 删除失败: HTTP {resp.status_code}")

    payload = resp.json()
    if payload.get("code") != 0:
        message = payload.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 删除失败: {message}")


async def _ragflow_get_chunk(dataset_id: str, document_id: str, chunk_id: str) -> Dict[str, Any]:
    """从 RAGFlow 查询单个 chunk 内容，用于“点击查看案例”。"""
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = _ragflow_headers()
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


async def _ragflow_create_chat_assistant(
    name: str,
    dataset_ids: List[str],
    model_name: Optional[str] = None,
    system_prompt: Optional[str] = None,
    top_n: Optional[int] = None,
    similarity_threshold: Optional[float] = None,
) -> str:
    """创建 RAGFlow 聊天助手，返回 chat_id。"""
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = _ragflow_headers()
    body: Dict[str, Any] = {
        "name": name,
        "dataset_ids": dataset_ids,
    }

    # 可选 LLM 配置（按需传入，避免参数不兼容）
    if model_name:
        body["llm"] = {"model_name": model_name}

    # 可选提示词配置
    if system_prompt or top_n is not None or similarity_threshold is not None:
        prompt: Dict[str, Any] = {}
        if system_prompt:
            prompt["prompt"] = system_prompt
        if top_n is not None:
            prompt["top_n"] = top_n
        if similarity_threshold is not None:
            prompt["similarity_threshold"] = similarity_threshold
        body["prompt"] = prompt

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{base_url}/api/v1/chats", json=body, headers=headers)

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 创建聊天助手失败: HTTP {resp.status_code}")

    payload = resp.json()
    if payload.get("code") != 0:
        message = payload.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 创建聊天助手失败: {message}")

    data = payload.get("data") or {}
    chat_id = data.get("id")
    if not chat_id:
        raise HTTPException(status_code=502, detail="RAGFlow 返回缺少 chat_id")

    return chat_id


async def _ragflow_create_session(chat_id: str, name: str, user_id: Optional[str]) -> str:
    """创建 RAGFlow 会话，返回 session_id。"""
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = _ragflow_headers()
    body: Dict[str, Any] = {"name": name}
    if user_id:
        body["user_id"] = user_id

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base_url}/api/v1/chats/{chat_id}/sessions",
            headers=headers,
            json=body,
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 创建会话失败: HTTP {resp.status_code}")

    payload = resp.json()
    if payload.get("code") != 0:
        message = payload.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 创建会话失败: {message}")

    data = payload.get("data") or {}
    session_id = data.get("id") or data.get("session_id")
    if not session_id:
        raise HTTPException(status_code=502, detail="RAGFlow 返回缺少 session_id")

    return session_id


async def _ragflow_update_chat_name(chat_id: str, new_name: str) -> None:
    """同步更新 RAGFlow 聊天助手名称。"""
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = _ragflow_headers()
    body = {"name": new_name}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"{base_url}/api/v1/chats/{chat_id}",
            headers=headers,
            json=body,
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 对话重命名失败: HTTP {resp.status_code}")

    payload = resp.json()
    if payload.get("code") != 0:
        message = payload.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 对话重命名失败: {message}")


async def _ragflow_update_session_name(chat_id: str, session_id: str, new_name: str) -> None:
    """同步更新 RAGFlow 会话名称。"""
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = _ragflow_headers()
    body = {"name": new_name}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"{base_url}/api/v1/chats/{chat_id}/sessions/{session_id}",
            headers=headers,
            json=body,
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 会话重命名失败: HTTP {resp.status_code}")

    payload = resp.json()
    if payload.get("code") != 0:
        message = payload.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 会话重命名失败: {message}")


async def _ragflow_update_chat_settings(chat_id: str, body: Dict[str, Any]) -> None:
    """同步更新 RAGFlow 聊天助手配置。"""
    if not body:
        return
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = _ragflow_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"{base_url}/api/v1/chats/{chat_id}",
            headers=headers,
            json=body,
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 更新对话设置失败: HTTP {resp.status_code}")

    payload = resp.json()
    if payload.get("code") != 0:
        message = payload.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 更新对话设置失败: {message}")


async def _ragflow_delete_chats(chat_ids: List[str]) -> None:
    """同步删除 RAGFlow 聊天助手。"""
    if not chat_ids:
        return
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = _ragflow_headers()
    body = {"ids": chat_ids}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            "DELETE",
            f"{base_url}/api/v1/chats",
            headers=headers,
            json=body,
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 删除对话失败: HTTP {resp.status_code}")

    payload = resp.json()
    if payload.get("code") != 0:
        message = payload.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 删除对话失败: {message}")


async def _ragflow_delete_sessions(chat_id: str, session_ids: List[str]) -> None:
    """同步删除 RAGFlow 会话。"""
    if not session_ids:
        return
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = _ragflow_headers()
    body = {"ids": session_ids}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            "DELETE",
            f"{base_url}/api/v1/chats/{chat_id}/sessions",
            headers=headers,
            json=body,
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 删除会话失败: HTTP {resp.status_code}")

    payload = resp.json()
    if payload.get("code") != 0:
        message = payload.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 删除会话失败: {message}")


async def _ragflow_chat_completion(
    chat_id: str,
    question: str,
    session_id: Optional[str],
    user_id: Optional[str],
    stream: bool = False,
    metadata_condition: Optional[dict] = None,
) -> Dict[str, Any]:
    """调用 RAGFlow 会话对话接口。"""
    base_url = str(settings.ragflow_base_url).rstrip("/")
    headers = _ragflow_headers()
    body: Dict[str, Any] = {
        "question": question,
        "stream": stream,
    }
    if session_id:
        body["session_id"] = session_id
    elif user_id:
        body["user_id"] = user_id
    if metadata_condition:
        body["metadata_condition"] = metadata_condition

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/api/v1/chats/{chat_id}/completions",
            headers=headers,
            json=body,
        )

    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"RAGFlow 对话失败: HTTP {resp.status_code}")

    payload = resp.json()
    if payload.get("code") != 0:
        message = payload.get("message", "未知错误")
        raise HTTPException(status_code=502, detail=f"RAGFlow 对话失败: {message}")

    data = payload.get("data") or {}
    return {"data": data, "raw": payload}


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


async def _resolve_document_scope(
    session: AsyncSession,
    role: str,
    user_id: int,
    kb_id: Optional[int],
    class_id: Optional[int],
    class_code: Optional[str],
) -> Dict[str, Optional[int]]:
    """解析文件列表的查询范围，并做权限校验。"""
    role = role.lower().strip()

    # 先将班级编号解析为 class_id
    if class_id is None and class_code is not None:
        cls = (
            await session.execute(
                select(models.Class).where(models.Class.class_code == class_code)
            )
        ).scalar_one_or_none()
        if not cls:
            raise HTTPException(status_code=404, detail="班级不存在")
        class_id = cls.id

    # 学生：只能查看自己班级
    if role == "student":
        student = (
            await session.execute(
                select(models.Student).where(models.Student.id == user_id)
            )
        ).scalar_one_or_none()
        if not student or student.status != 1:
            raise HTTPException(status_code=403, detail="学生不存在或已停用")

        if class_id is not None and class_id != student.class_id:
            raise HTTPException(status_code=403, detail="无权访问该班级")

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
            return {"kb_id": kb.id, "class_id": None}

        return {"kb_id": None, "class_id": student.class_id}

    # 教师：若有多个班级，必须选择
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
            return {"kb_id": kb.id, "class_id": None}

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
            await session.execute(
                select(models.Class).where(models.Class.id == class_id)
            )
        ).scalar_one_or_none()
        if not cls:
            raise HTTPException(status_code=404, detail="班级不存在")
        if cls.teacher_id != teacher.id:
            raise HTTPException(status_code=403, detail="无权访问该班级")

        return {"kb_id": None, "class_id": cls.id}

    # 管理员：可查看任意班级/知识库
    if role == "admin":
        if kb_id is not None:
            kb = (
                await session.execute(
                    select(models.KnowledgeBase).where(models.KnowledgeBase.id == kb_id)
                )
            ).scalar_one_or_none()
            if not kb:
                raise HTTPException(status_code=404, detail="知识库不存在")
            return {"kb_id": kb.id, "class_id": None}
        if class_id is not None:
            cls = (
                await session.execute(
                    select(models.Class).where(models.Class.id == class_id)
                )
            ).scalar_one_or_none()
            if not cls:
                raise HTTPException(status_code=404, detail="班级不存在")
            return {"kb_id": None, "class_id": cls.id}
        return {"kb_id": None, "class_id": None}

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


def _parse_date(value: Optional[str], field_name: str) -> Optional[datetime]:
    """解析日期字符串（支持 YYYY-MM-DD 或 ISO8601）。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field_name} 格式错误")


async def _resolve_class_id(
    session: AsyncSession,
    class_id: Optional[int],
    class_code: Optional[str],
) -> Optional[int]:
    """将班级编号解析为班级 ID（用于过滤统计范围）。"""
    if class_id is None and class_code is not None:
        cls = (
            await session.execute(
                select(models.Class).where(models.Class.class_code == class_code)
            )
        ).scalar_one_or_none()
        if not cls:
            raise HTTPException(status_code=404, detail="班级不存在")
        class_id = cls.id
    return class_id


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


@app.post("/auth/student/register")
async def student_register(
    payload: StudentRegisterRequest,
    session: AsyncSession = Depends(get_session),
):
    """学生注册（学号 + 密码 + 班级编号 + 姓名）。"""
    student_no = (payload.student_no or "").strip()
    password = (payload.password or "").strip()
    class_code = (payload.class_code or "").strip()
    name = (payload.name or "").strip()

    if not student_no or not password or not class_code or not name:
        raise HTTPException(status_code=400, detail="学号、密码、班级编号、姓名不能为空")

    cls = (
        await session.execute(
            select(models.Class).where(models.Class.class_code == class_code)
        )
    ).scalar_one_or_none()
    if not cls:
        raise HTTPException(status_code=404, detail="班级不存在")

    # 班级必须已绑定教师，且教师状态为启用
    teacher = (
        await session.execute(
            select(models.Teacher).where(models.Teacher.id == cls.teacher_id)
        )
    ).scalar_one_or_none()
    if not teacher or teacher.status != 1:
        raise HTTPException(status_code=400, detail="班级未绑定启用教师")

    exists = (
        await session.execute(
            select(models.Student).where(models.Student.student_no == student_no)
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="学号已存在")

    student = models.Student(
        student_no=student_no,
        class_id=cls.id,
        password_hash=_hash_password(password),
        name=name,
        status=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(student)
    try:
        await session.commit()
        await session.refresh(student)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="学号已存在")

    return {
        "role": "student",
        "id": student.id,
        "student_no": student.student_no,
        "name": student.name,
        "class_id": cls.id,
        "class_code": cls.class_code,
        "class_name": cls.class_name,
    }


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

    # 同班级同名禁止（排除已拒绝的记录）
    reuse_doc: Optional[models.Document] = None
    if file.filename:
        exists = (
            await session.execute(
                select(models.Document).where(
                    models.Document.kb_id == kb.id,
                    models.Document.original_name == file.filename,
                )
            )
        ).scalar_one_or_none()
        if exists and exists.status != models.DocumentStatus.rejected:
            raise HTTPException(status_code=409, detail="同名文件已存在，请更换文件名后再上传")
        if exists and exists.status == models.DocumentStatus.rejected:
            reuse_doc = exists

    client = _get_minio_client()
    pending_bucket = settings.minio_bucket_pending
    kb_bucket = settings.minio_bucket_kb
    _ensure_bucket(client, pending_bucket)
    _ensure_bucket(client, kb_bucket)

    # 如果是被拒绝的旧文件，先清理旧对象，避免冗余占用
    if reuse_doc and reuse_doc.storage_path:
        old_bucket = (
            pending_bucket
            if reuse_doc.status in {models.DocumentStatus.pending, models.DocumentStatus.rejected}
            else kb_bucket
        )
        _safe_remove_minio_object(client, old_bucket, reuse_doc.storage_path)

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

    # 计算文件内容哈希（用于同内容提醒）
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    size_bytes = len(data)
    content_hash = hashlib.sha256(data).hexdigest()

    content_type = (
        file.content_type
        or mimetypes.guess_type(file.filename or "")[0]
        or "application/octet-stream"
    )
    try:
        client.put_object(
            target_bucket,
            object_name,
            io.BytesIO(data),
            length=size_bytes,
            part_size=10 * 1024 * 1024,
            content_type=content_type,
        )
    except S3Error as exc:
        raise HTTPException(status_code=500, detail=f"MinIO 上传失败: {exc.code}")

    duplicate_stmt = select(models.Document).where(
        models.Document.kb_id == kb.id,
        models.Document.content_hash == content_hash,
    )
    if reuse_doc:
        duplicate_stmt = duplicate_stmt.where(models.Document.id != reuse_doc.id)
    duplicate_doc = (await session.execute(duplicate_stmt)).scalar_one_or_none()

    if reuse_doc:
        # 复用已拒绝的记录，避免同名唯一约束冲突
        doc = reuse_doc
        doc.filename = object_name
        doc.original_name = file.filename or ""
        doc.uploader_student_id = uploader_id if role == "student" else None
        doc.uploader_teacher_id = uploader_id if role == "teacher" else None
        doc.uploader_admin_id = uploader_id if role == "admin" else None
        doc.size_bytes = size_bytes
        doc.mime_type = content_type
        doc.content_hash = content_hash
        doc.ragflow_document_id = None
        doc.status = status
        doc.storage_path = object_name
        doc.uploaded_at = datetime.utcnow()
        doc.updated_at = datetime.utcnow()
    else:
        doc = models.Document(
            kb_id=kb.id,
            filename=object_name,
            original_name=file.filename or "",
            uploader_student_id=uploader_id if role == "student" else None,
            uploader_teacher_id=uploader_id if role == "teacher" else None,
            uploader_admin_id=uploader_id if role == "admin" else None,
            size_bytes=size_bytes,
            mime_type=content_type,
            content_hash=content_hash,
            ragflow_document_id=None,
            status=status,
            storage_path=object_name,
            uploaded_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(doc)

    try:
        await session.commit()
        await session.refresh(doc)
    except IntegrityError:
        await session.rollback()
        _safe_remove_minio_object(client, target_bucket, object_name)
        raise HTTPException(status_code=409, detail="同名文件已存在，请更换文件名后再上传")

    return {
        "id": doc.id,
        "kb_id": doc.kb_id,
        "status": doc.status.value,
        "filename": doc.original_name,
        "content_duplicate": bool(duplicate_doc),
        "duplicate_document_id": duplicate_doc.id if duplicate_doc else None,
        "duplicate_document_name": duplicate_doc.original_name if duplicate_doc else None,
    }


@app.get("/documents")
async def list_documents(
    role: str,
    user_id: int,
    kb_id: Optional[int] = None,
    class_id: Optional[int] = None,
    class_code: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    filename: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """文件列表（支持分页与权限控制）。"""
    role = role.lower().strip()
    if role not in {"student", "teacher", "admin"}:
        raise HTTPException(status_code=400, detail="role 参数不合法")

    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(status_code=400, detail="分页参数不合法")

    scope = await _resolve_document_scope(
        session,
        role,
        user_id,
        kb_id,
        class_id,
        class_code,
    )

    # 状态过滤：默认仅展示已通过/已嵌入文件
    if status:
        raw_statuses = [s.strip() for s in status.split(",") if s.strip()]
        try:
            status_values = [models.DocumentStatus(s) for s in raw_statuses]
        except ValueError:
            raise HTTPException(status_code=400, detail="status 参数不合法")
    else:
        status_values = [models.DocumentStatus.approved, models.DocumentStatus.embedded]

    # 非管理员不允许查看拒绝/待审核
    if role != "admin":
        forbidden = {models.DocumentStatus.pending, models.DocumentStatus.rejected}
        if any(s in forbidden for s in status_values):
            raise HTTPException(status_code=403, detail="无权查看该状态的文件")

    base_stmt = (
        select(models.Document, models.KnowledgeBase, models.Class)
        .join(models.KnowledgeBase, models.Document.kb_id == models.KnowledgeBase.id)
        .join(models.Class, models.KnowledgeBase.class_id == models.Class.id)
        .where(models.Document.status.in_(status_values))
    )

    if scope.get("kb_id"):
        base_stmt = base_stmt.where(models.Document.kb_id == scope["kb_id"])
    elif scope.get("class_id"):
        base_stmt = base_stmt.where(models.KnowledgeBase.class_id == scope["class_id"])

    keyword = (filename or "").strip()
    if keyword:
        base_stmt = base_stmt.where(models.Document.original_name.like(f"%{keyword}%"))

    total_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await session.execute(total_stmt)).scalar_one()

    rows = (
        await session.execute(
            base_stmt
            .order_by(models.Document.uploaded_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items = [
        {
            "document_id": doc.id,
            "document_name": doc.original_name,
            "status": doc.status.value,
            "uploaded_at": doc.uploaded_at,
            "ragflow_document_id": doc.ragflow_document_id,
            "kb_id": kb.id,
            "class_id": cls.id,
            "class_code": cls.class_code,
            "class_name": cls.class_name,
            "uploader_student_id": doc.uploader_student_id,
            "uploader_teacher_id": doc.uploader_teacher_id,
            "uploader_admin_id": doc.uploader_admin_id,
        }
        for doc, kb, cls in rows
    ]

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": items,
    }


@app.get("/documents/{document_id}")
async def get_document_detail(
    document_id: int,
    role: str,
    user_id: int,
    session: AsyncSession = Depends(get_session),
):
    """文件详情（用于前端详情页/预览准备）。"""
    role = role.lower().strip()
    if role not in {"student", "teacher", "admin"}:
        raise HTTPException(status_code=400, detail="role 参数不合法")

    row = (
        await session.execute(
            select(models.Document, models.KnowledgeBase, models.Class)
            .join(models.KnowledgeBase, models.Document.kb_id == models.KnowledgeBase.id)
            .join(models.Class, models.KnowledgeBase.class_id == models.Class.id)
            .where(models.Document.id == document_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")

    doc, kb, cls = row
    await _check_document_permission(session, role, user_id, cls)

    # 非管理员不允许查看待审核/已拒绝
    if role != "admin" and doc.status in {
        models.DocumentStatus.pending,
        models.DocumentStatus.rejected,
    }:
        raise HTTPException(status_code=403, detail="无权查看该状态的文件")

    uploader = None
    if doc.uploader_student_id:
        student = (
            await session.execute(
                select(models.Student).where(models.Student.id == doc.uploader_student_id)
            )
        ).scalar_one_or_none()
        if student:
            uploader = {
                "role": "student",
                "id": student.id,
                "no": student.student_no,
                "name": student.name,
            }
    elif doc.uploader_teacher_id:
        teacher = (
            await session.execute(
                select(models.Teacher).where(models.Teacher.id == doc.uploader_teacher_id)
            )
        ).scalar_one_or_none()
        if teacher:
            uploader = {
                "role": "teacher",
                "id": teacher.id,
                "no": teacher.teacher_no,
                "name": teacher.name,
            }
    elif doc.uploader_admin_id:
        admin = (
            await session.execute(
                select(models.Admin).where(models.Admin.id == doc.uploader_admin_id)
            )
        ).scalar_one_or_none()
        if admin:
            uploader = {
                "role": "admin",
                "id": admin.id,
                "no": admin.admin_no,
                "name": admin.name,
            }

    audit_rows = (
        await session.execute(
            select(models.DocumentAudit, models.Admin)
            .join(models.Admin, models.DocumentAudit.reviewer_admin_id == models.Admin.id)
            .where(models.DocumentAudit.document_id == doc.id)
            .order_by(models.DocumentAudit.decided_at.desc())
        )
    ).all()
    audits = [
        {
            "id": audit.id,
            "decision": audit.decision.value,
            "reason": audit.reason,
            "decided_at": audit.decided_at,
            "reviewer_admin_id": admin.id,
            "reviewer_admin_name": admin.name,
        }
        for audit, admin in audit_rows
    ]

    task_rows = (
        await session.execute(
            select(models.EmbeddingTask)
            .where(models.EmbeddingTask.document_id == doc.id)
            .order_by(models.EmbeddingTask.started_at.desc())
        )
    ).scalars().all()
    embedding_tasks = [
        {
            "id": task.id,
            "status": task.status.value,
            "chunk_method": task.chunk_method,
            "ragflow_task_id": task.ragflow_task_id,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "message": task.message,
        }
        for task in task_rows
    ]

    return {
        "document_id": doc.id,
        "document_name": doc.original_name,
        "status": doc.status.value,
        "kb_id": kb.id,
        "class_id": cls.id,
        "class_code": cls.class_code,
        "class_name": cls.class_name,
        "ragflow_dataset_id": kb.ragflow_dataset_id,
        "ragflow_document_id": doc.ragflow_document_id,
        "size_bytes": doc.size_bytes,
        "mime_type": doc.mime_type,
        "uploaded_at": doc.uploaded_at,
        "updated_at": doc.updated_at,
        "uploader": uploader,
        "audits": audits,
        "embedding_tasks": embedding_tasks,
        "content_url": f"/documents/{doc.id}/content",
    }


@app.get("/documents/{document_id}/content")
async def get_document_content(
    document_id: int,
    role: str,
    user_id: int,
    download: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """文件内容下载/预览（前端用于 Excel 预览）。"""
    role = role.lower().strip()
    if role not in {"student", "teacher", "admin"}:
        raise HTTPException(status_code=400, detail="role 参数不合法")

    row = (
        await session.execute(
            select(models.Document, models.KnowledgeBase, models.Class)
            .join(models.KnowledgeBase, models.Document.kb_id == models.KnowledgeBase.id)
            .join(models.Class, models.KnowledgeBase.class_id == models.Class.id)
            .where(models.Document.id == document_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")

    doc, kb, cls = row
    await _check_document_permission(session, role, user_id, cls)

    # 非管理员不允许下载待审核/已拒绝文件
    if role != "admin" and doc.status in {
        models.DocumentStatus.pending,
        models.DocumentStatus.rejected,
    }:
        raise HTTPException(status_code=403, detail="无权下载该状态的文件")

    if not doc.storage_path:
        raise HTTPException(status_code=404, detail="文件存储路径缺失")

    client = _get_minio_client()
    pending_bucket = settings.minio_bucket_pending
    kb_bucket = settings.minio_bucket_kb
    _ensure_bucket(client, pending_bucket)
    _ensure_bucket(client, kb_bucket)
    target_bucket = (
        pending_bucket
        if doc.status in {models.DocumentStatus.pending, models.DocumentStatus.rejected}
        else kb_bucket
    )

    data = _read_minio_object(client, target_bucket, doc.storage_path)
    filename = doc.original_name or doc.filename or f"document-{doc.id}"
    content_type = doc.mime_type or "application/octet-stream"
    disposition = "attachment" if download else "inline"
    headers = {"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename)}"}

    return StreamingResponse(
        io.BytesIO(data),
        media_type=content_type,
        headers=headers,
    )


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


@app.put("/documents/{document_id}/rename")
async def rename_document(
    document_id: int,
    payload: RenameDocumentRequest,
    session: AsyncSession = Depends(get_session),
):
    """重命名文件（仅更新展示名称，不改存储路径）。"""
    role = payload.role.lower().strip()
    if role not in {"teacher", "admin"}:
        raise HTTPException(status_code=403, detail="仅教师或管理员可重命名文件")

    new_name = (payload.new_name or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="new_name 不能为空")
    if len(new_name) > 255:
        raise HTTPException(status_code=400, detail="new_name 过长")

    row = (
        await session.execute(
            select(models.Document, models.KnowledgeBase, models.Class)
            .join(models.KnowledgeBase, models.Document.kb_id == models.KnowledgeBase.id)
            .join(models.Class, models.KnowledgeBase.class_id == models.Class.id)
            .where(models.Document.id == document_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")

    doc, kb, cls = row

    if role == "teacher":
        teacher = (
            await session.execute(
                select(models.Teacher).where(models.Teacher.id == payload.user_id)
            )
        ).scalar_one_or_none()
        if not teacher or teacher.status != 1:
            raise HTTPException(status_code=403, detail="教师不存在或已停用")
        if cls.teacher_id != teacher.id:
            raise HTTPException(status_code=403, detail="无权操作该班级文件")

    if role == "admin":
        admin = (
            await session.execute(
                select(models.Admin).where(models.Admin.id == payload.user_id)
            )
        ).scalar_one_or_none()
        if not admin or admin.status != 1:
            raise HTTPException(status_code=403, detail="管理员不存在或已停用")

    # 同步更新 RAGFlow 文档名称（如果存在）
    if payload.sync_ragflow and doc.ragflow_document_id:
        await _ragflow_update_document_name(
            kb.ragflow_dataset_id,
            doc.ragflow_document_id,
            new_name,
        )

    doc.original_name = new_name
    doc.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(doc)

    return {
        "document_id": doc.id,
        "document_name": doc.original_name,
        "kb_id": doc.kb_id,
        "class_id": cls.id,
        "class_code": cls.class_code,
        "class_name": cls.class_name,
        "ragflow_document_id": doc.ragflow_document_id,
        "updated_at": doc.updated_at,
    }


@app.delete("/documents/{document_id}")
async def delete_document(
    document_id: int,
    payload: DeleteDocumentRequest,
    session: AsyncSession = Depends(get_session),
):
    """删除文件（数据库 + MinIO + 可选同步 RAGFlow）。"""
    role = payload.role.lower().strip()
    if role not in {"teacher", "admin"}:
        raise HTTPException(status_code=403, detail="仅教师或管理员可删除文件")

    row = (
        await session.execute(
            select(models.Document, models.KnowledgeBase, models.Class)
            .join(models.KnowledgeBase, models.Document.kb_id == models.KnowledgeBase.id)
            .join(models.Class, models.KnowledgeBase.class_id == models.Class.id)
            .where(models.Document.id == document_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")

    doc, kb, cls = row

    if role == "teacher":
        teacher = (
            await session.execute(
                select(models.Teacher).where(models.Teacher.id == payload.user_id)
            )
        ).scalar_one_or_none()
        if not teacher or teacher.status != 1:
            raise HTTPException(status_code=403, detail="教师不存在或已停用")
        if cls.teacher_id != teacher.id:
            raise HTTPException(status_code=403, detail="无权删除该班级文件")

    if role == "admin":
        admin = (
            await session.execute(
                select(models.Admin).where(models.Admin.id == payload.user_id)
            )
        ).scalar_one_or_none()
        if not admin or admin.status != 1:
            raise HTTPException(status_code=403, detail="管理员不存在或已停用")

    # 1) 先同步删除 RAGFlow 文档（若有）
    if payload.sync_ragflow and doc.ragflow_document_id:
        await _ragflow_delete_documents(kb.ragflow_dataset_id, [doc.ragflow_document_id])

    # 2) 删除 MinIO 对象（按状态选择桶）
    if payload.remove_minio and doc.storage_path:
        client = _get_minio_client()
        pending_bucket = settings.minio_bucket_pending
        kb_bucket = settings.minio_bucket_kb
        _ensure_bucket(client, pending_bucket)
        _ensure_bucket(client, kb_bucket)
        target_bucket = (
            pending_bucket
            if doc.status in {models.DocumentStatus.pending, models.DocumentStatus.rejected}
            else kb_bucket
        )
        _safe_remove_minio_object(client, target_bucket, doc.storage_path)

    # 3) 删除关联记录（避免外键约束）
    await session.execute(
        delete(models.DocumentAudit).where(models.DocumentAudit.document_id == doc.id)
    )
    await session.execute(
        delete(models.DocumentVersion).where(models.DocumentVersion.document_id == doc.id)
    )
    await session.execute(
        delete(models.EmbeddingTask).where(models.EmbeddingTask.document_id == doc.id)
    )

    # 4) 删除文档记录
    await session.delete(doc)
    await session.commit()

    return {
        "document_id": document_id,
        "deleted": True,
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


@app.get("/audits")
async def list_audits(
    admin_id: int,
    class_id: Optional[int] = None,
    class_code: Optional[str] = None,
    decision: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    filename: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """审核记录列表（仅管理员可查看）。"""
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(status_code=400, detail="分页参数不合法")

    admin = (
        await session.execute(
            select(models.Admin).where(models.Admin.id == admin_id)
        )
    ).scalar_one_or_none()
    if not admin or admin.status != 1:
        raise HTTPException(status_code=403, detail="管理员不存在或已停用")

    if class_id is None and class_code is not None:
        cls = (
            await session.execute(
                select(models.Class).where(models.Class.class_code == class_code)
            )
        ).scalar_one_or_none()
        if not cls:
            raise HTTPException(status_code=404, detail="班级不存在")
        class_id = cls.id

    if decision:
        decision = decision.lower().strip()
        if decision not in {"approved", "rejected"}:
            raise HTTPException(status_code=400, detail="decision 参数不合法")

    base_stmt = (
        select(
            models.DocumentAudit,
            models.Document,
            models.KnowledgeBase,
            models.Class,
            models.Admin,
        )
        .join(models.Document, models.DocumentAudit.document_id == models.Document.id)
        .join(models.KnowledgeBase, models.Document.kb_id == models.KnowledgeBase.id)
        .join(models.Class, models.KnowledgeBase.class_id == models.Class.id)
        .join(models.Admin, models.DocumentAudit.reviewer_admin_id == models.Admin.id)
    )

    if class_id is not None:
        base_stmt = base_stmt.where(models.Class.id == class_id)

    if decision:
        base_stmt = base_stmt.where(
            models.DocumentAudit.decision == models.AuditDecision(decision)
        )

    keyword = (filename or "").strip()
    if keyword:
        base_stmt = base_stmt.where(models.Document.original_name.like(f"%{keyword}%"))

    total_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await session.execute(total_stmt)).scalar_one()

    rows = (
        await session.execute(
            base_stmt
            .order_by(models.DocumentAudit.decided_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items = [
        {
            "audit_id": audit.id,
            "document_id": doc.id,
            "document_name": doc.original_name,
            "document_status": doc.status.value,
            "decision": audit.decision.value,
            "reason": audit.reason,
            "decided_at": audit.decided_at,
            "reviewer_admin_id": reviewer.id,
            "reviewer_admin_name": reviewer.name,
            "kb_id": kb.id,
            "class_id": cls.id,
            "class_code": cls.class_code,
            "class_name": cls.class_name,
            "uploader_student_id": doc.uploader_student_id,
            "uploader_teacher_id": doc.uploader_teacher_id,
            "uploader_admin_id": doc.uploader_admin_id,
            "uploaded_at": doc.uploaded_at,
        }
        for audit, doc, kb, cls, reviewer in rows
    ]

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": items,
    }


@app.get("/audits/{audit_id}")
async def get_audit_detail(
    audit_id: int,
    admin_id: int,
    session: AsyncSession = Depends(get_session),
):
    """审核记录详情（仅管理员可查看）。"""
    admin = (
        await session.execute(
            select(models.Admin).where(models.Admin.id == admin_id)
        )
    ).scalar_one_or_none()
    if not admin or admin.status != 1:
        raise HTTPException(status_code=403, detail="管理员不存在或已停用")

    row = (
        await session.execute(
            select(
                models.DocumentAudit,
                models.Document,
                models.KnowledgeBase,
                models.Class,
                models.Admin,
            )
            .join(models.Document, models.DocumentAudit.document_id == models.Document.id)
            .join(models.KnowledgeBase, models.Document.kb_id == models.KnowledgeBase.id)
            .join(models.Class, models.KnowledgeBase.class_id == models.Class.id)
            .join(models.Admin, models.DocumentAudit.reviewer_admin_id == models.Admin.id)
            .where(models.DocumentAudit.id == audit_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="审核记录不存在")

    audit, doc, kb, cls, reviewer = row

    uploader = None
    if doc.uploader_student_id:
        student = (
            await session.execute(
                select(models.Student).where(models.Student.id == doc.uploader_student_id)
            )
        ).scalar_one_or_none()
        if student:
            uploader = {
                "role": "student",
                "id": student.id,
                "no": student.student_no,
                "name": student.name,
            }
    elif doc.uploader_teacher_id:
        teacher = (
            await session.execute(
                select(models.Teacher).where(models.Teacher.id == doc.uploader_teacher_id)
            )
        ).scalar_one_or_none()
        if teacher:
            uploader = {
                "role": "teacher",
                "id": teacher.id,
                "no": teacher.teacher_no,
                "name": teacher.name,
            }
    elif doc.uploader_admin_id:
        admin_uploader = (
            await session.execute(
                select(models.Admin).where(models.Admin.id == doc.uploader_admin_id)
            )
        ).scalar_one_or_none()
        if admin_uploader:
            uploader = {
                "role": "admin",
                "id": admin_uploader.id,
                "no": admin_uploader.admin_no,
                "name": admin_uploader.name,
            }

    return {
        "audit_id": audit.id,
        "decision": audit.decision.value,
        "reason": audit.reason,
        "decided_at": audit.decided_at,
        "reviewer_admin_id": reviewer.id,
        "reviewer_admin_name": reviewer.name,
        "document": {
            "document_id": doc.id,
            "document_name": doc.original_name,
            "status": doc.status.value,
            "kb_id": kb.id,
            "class_id": cls.id,
            "class_code": cls.class_code,
            "class_name": cls.class_name,
            "ragflow_document_id": doc.ragflow_document_id,
            "uploaded_at": doc.uploaded_at,
            "content_url": f"/documents/{doc.id}/content",
        },
        "uploader": uploader,
    }


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
    headers = _ragflow_headers()
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
# 对话模块（RAGFlow 会话接口）
# ------------------------------


@app.post("/conversations")
async def create_conversation(
    payload: CreateConversationRequest,
    session: AsyncSession = Depends(get_session),
):
    """创建对话（同步创建 RAGFlow 聊天助手 + 会话）。"""
    role = payload.role.lower().strip()
    if role not in {"teacher", "student"}:
        raise HTTPException(status_code=403, detail="仅教师或学生可创建对话")

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

    cls = (
        await session.execute(
            select(models.Class).where(models.Class.id == kb.class_id)
        )
    ).scalar_one_or_none()
    if not cls:
        raise HTTPException(status_code=404, detail="班级不存在")

    name = (payload.name or "").strip()
    if not name:
        name = f"{cls.class_code}-对话-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    # 避免 RAGFlow 聊天助手名称重复，追加短随机串作为唯一标识
    unique_chat_name = f"{cls.class_code}-{name}-{uuid.uuid4().hex[:8]}"
    system_prompt = None
    if payload.system_prompt is not None:
        system_prompt = _normalize_system_prompt(payload.system_prompt)

    chat_id = await _ragflow_create_chat_assistant(
        name=unique_chat_name,
        dataset_ids=[kb.ragflow_dataset_id],
        model_name=payload.model_name,
        system_prompt=system_prompt,
        top_n=payload.top_n,
        similarity_threshold=payload.similarity_threshold,
    )
    session_id = await _ragflow_create_session(
        chat_id=chat_id,
        name=name,
        user_id=str(payload.user_id),
    )

    conv = models.Conversation(
        owner_teacher_id=payload.user_id if role == "teacher" else None,
        owner_student_id=payload.user_id if role == "student" else None,
        kb_id=kb.id,
        name=name,
        ragflow_chat_id=chat_id,
        ragflow_session_id=session_id,
        model_name=payload.model_name,
        top_n=payload.top_n,
        similarity_threshold=payload.similarity_threshold,
        show_citations=payload.show_citations,
        system_prompt=system_prompt,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(conv)
    await session.commit()
    await session.refresh(conv)

    return {
        "conversation_id": conv.id,
        "ragflow_chat_id": conv.ragflow_chat_id,
        "ragflow_session_id": conv.ragflow_session_id,
        "kb_id": kb.id,
        "class_id": cls.id,
        "class_code": cls.class_code,
        "class_name": cls.class_name,
        "name": conv.name,
        "created_at": conv.created_at,
    }


@app.get("/conversations")
async def list_conversations(
    role: str,
    user_id: int,
    class_id: Optional[int] = None,
    class_code: Optional[str] = None,
    kb_id: Optional[int] = None,
    keyword: Optional[str] = None,
    include_last_message: bool = False,
    page: int = 1,
    page_size: int = 20,
    session: AsyncSession = Depends(get_session),
):
    """对话列表（支持班级筛选/关键词搜索）。"""
    role = role.lower().strip()
    if role not in {"teacher", "student"}:
        raise HTTPException(status_code=403, detail="仅教师或学生可查看对话")
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(status_code=400, detail="分页参数不合法")

    class_id = await _resolve_class_id(session, class_id, class_code)

    stmt = (
        select(models.Conversation, models.KnowledgeBase, models.Class)
        .join(models.KnowledgeBase, models.Conversation.kb_id == models.KnowledgeBase.id)
        .join(models.Class, models.KnowledgeBase.class_id == models.Class.id)
    )

    if role == "teacher":
        stmt = stmt.where(models.Conversation.owner_teacher_id == user_id)
    else:
        stmt = stmt.where(models.Conversation.owner_student_id == user_id)

    if kb_id is not None:
        stmt = stmt.where(models.Conversation.kb_id == kb_id)
    if class_id is not None:
        stmt = stmt.where(models.KnowledgeBase.class_id == class_id)
    if keyword:
        # 关键词搜索仅针对对话名称
        stmt = stmt.where(models.Conversation.name.like(f"%{keyword.strip()}%"))

    total_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(total_stmt)).scalar_one()

    rows = (
        await session.execute(
            stmt
            .order_by(models.Conversation.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    # 可选：补充每个对话的最后一条消息（用于列表预览）
    last_message_map: Dict[int, Dict[str, Any]] = {}
    if include_last_message and rows:
        conv_ids = [conv.id for conv, _, _ in rows]
        msg_rows = (
            await session.execute(
                select(models.Message)
                .where(models.Message.conversation_id.in_(conv_ids))
                .order_by(models.Message.conversation_id, models.Message.created_at.desc())
            )
        ).scalars().all()
        for msg in msg_rows:
            if msg.conversation_id not in last_message_map:
                last_message_map[msg.conversation_id] = {
                    "role": msg.sender_role.value,
                    "content": msg.content,
                    "created_at": msg.created_at,
                }

    items = [
        {
            "conversation_id": conv.id,
            "name": conv.name or f"对话-{conv.id}",
            "kb_id": kb_row.id,
            "class_id": cls.id,
            "class_code": cls.class_code,
            "class_name": cls.class_name,
            "model_name": conv.model_name,
            "top_n": conv.top_n,
            "similarity_threshold": float(conv.similarity_threshold),
            "show_citations": conv.show_citations,
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
            "last_message": last_message_map.get(conv.id),
        }
        for conv, kb_row, cls in rows
    ]

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": items,
    }


@app.get("/conversations/{conversation_id}")
async def get_conversation_detail(
    conversation_id: int,
    role: str,
    user_id: int,
    session: AsyncSession = Depends(get_session),
):
    """对话详情（含消息列表）。"""
    role = role.lower().strip()
    if role not in {"teacher", "student"}:
        raise HTTPException(status_code=403, detail="仅教师或学生可查看对话")

    row = (
        await session.execute(
            select(models.Conversation, models.KnowledgeBase, models.Class)
            .join(models.KnowledgeBase, models.Conversation.kb_id == models.KnowledgeBase.id)
            .join(models.Class, models.KnowledgeBase.class_id == models.Class.id)
            .where(models.Conversation.id == conversation_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="对话不存在")

    conv, kb_row, cls = row
    if role == "teacher" and conv.owner_teacher_id != user_id:
        raise HTTPException(status_code=403, detail="无权查看该对话")
    if role == "student" and conv.owner_student_id != user_id:
        raise HTTPException(status_code=403, detail="无权查看该对话")

    msg_rows = (
        await session.execute(
            select(models.Message)
            .where(models.Message.conversation_id == conv.id)
            .order_by(models.Message.created_at.asc())
        )
    ).scalars().all()

    messages = [
        {
            "id": m.id,
            "role": m.sender_role.value,
            "content": m.content,
            "reference": m.reference,
            "created_at": m.created_at,
        }
        for m in msg_rows
    ]

    return {
        "conversation_id": conv.id,
        "name": conv.name or f"对话-{conv.id}",
        "kb_id": kb_row.id,
        "class_id": cls.id,
        "class_code": cls.class_code,
        "class_name": cls.class_name,
        "model_name": conv.model_name,
        "top_n": conv.top_n,
        "similarity_threshold": float(conv.similarity_threshold),
        "show_citations": conv.show_citations,
        "system_prompt": conv.system_prompt,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
        "messages": messages,
    }


@app.post("/conversations/{conversation_id}/messages")
async def send_conversation_message(
    conversation_id: int,
    payload: SendMessageRequest,
    session: AsyncSession = Depends(get_session),
):
    """发送消息并调用 RAGFlow 生成回复。"""
    role = payload.role.lower().strip()
    if role not in {"teacher", "student"}:
        raise HTTPException(status_code=403, detail="仅教师或学生可发送消息")

    content = (payload.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    conv = (
        await session.execute(
            select(models.Conversation).where(models.Conversation.id == conversation_id)
        )
    ).scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")

    if role == "teacher" and conv.owner_teacher_id != payload.user_id:
        raise HTTPException(status_code=403, detail="无权操作该对话")
    if role == "student" and conv.owner_student_id != payload.user_id:
        raise HTTPException(status_code=403, detail="无权操作该对话")

    # 记录用户消息
    user_msg = models.Message(
        conversation_id=conv.id,
        sender_role=models.SenderRole.user,
        content=content,
        reference=None,
        created_at=datetime.utcnow(),
    )
    session.add(user_msg)
    await session.commit()
    await session.refresh(user_msg)

    # 若缺少 RAGFlow chat/session，补建
    if not conv.ragflow_chat_id:
        kb = (
            await session.execute(
                select(models.KnowledgeBase).where(models.KnowledgeBase.id == conv.kb_id)
            )
        ).scalar_one_or_none()
        if not kb or not kb.ragflow_dataset_id:
            raise HTTPException(status_code=400, detail="知识库未绑定 RAGFlow dataset")
        chat_id = await _ragflow_create_chat_assistant(
            name=f"kb-{kb.id}-对话",
            dataset_ids=[kb.ragflow_dataset_id],
            model_name=conv.model_name,
            system_prompt=conv.system_prompt,
            top_n=conv.top_n,
            similarity_threshold=float(conv.similarity_threshold),
        )
        conv.ragflow_chat_id = chat_id

    if not conv.ragflow_session_id:
        conv.ragflow_session_id = await _ragflow_create_session(
            chat_id=conv.ragflow_chat_id,
            name=f"conv-{conv.id}",
            user_id=str(payload.user_id),
        )

    ragflow_result = await _ragflow_chat_completion(
        chat_id=conv.ragflow_chat_id,
        question=content,
        session_id=conv.ragflow_session_id,
        user_id=str(payload.user_id),
        stream=False,
        metadata_condition=payload.metadata_condition,
    )

    data = ragflow_result.get("data") or {}
    ragflow_session_id = data.get("session_id")
    if ragflow_session_id:
        conv.ragflow_session_id = ragflow_session_id

    # 尽量提取回答文本
    answer = ""
    if isinstance(data, dict):
        for key in ("answer", "content", "result", "response", "text"):
            if data.get(key):
                answer = data.get(key)
                break
        if not answer and isinstance(data.get("choices"), list):
            choice = data["choices"][0] if data["choices"] else None
            if isinstance(choice, dict):
                msg = choice.get("message") or choice.get("delta") or {}
                if isinstance(msg, dict) and msg.get("content"):
                    answer = msg.get("content")

    reference = None
    if isinstance(data, dict):
        if data.get("reference") or data.get("references"):
            reference = data.get("reference") or data.get("references")
        elif data.get("chunks") or data.get("doc_aggs"):
            reference = {
                "chunks": data.get("chunks"),
                "doc_aggs": data.get("doc_aggs"),
            }

    assistant_msg = models.Message(
        conversation_id=conv.id,
        sender_role=models.SenderRole.assistant,
        content=answer or "",
        reference=reference,
        created_at=datetime.utcnow(),
    )
    session.add(assistant_msg)
    conv.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(assistant_msg)

    return {
        "conversation_id": conv.id,
        "user_message_id": user_msg.id,
        "assistant_message_id": assistant_msg.id,
        "assistant_answer": assistant_msg.content,
        "reference": assistant_msg.reference,
    }


@app.put("/conversations/{conversation_id}/settings")
async def update_conversation_settings(
    conversation_id: int,
    payload: UpdateConversationSettingsRequest,
    session: AsyncSession = Depends(get_session),
):
    """更新对话设置（模型/提示词/检索参数等）。"""
    conv = await _get_conversation_for_owner(
        session, conversation_id, payload.role, payload.user_id
    )

    # 同步 RAGFlow 配置（可选）
    normalized_prompt = None
    if payload.system_prompt is not None:
        normalized_prompt = _normalize_system_prompt(payload.system_prompt)

    if payload.sync_ragflow and conv.ragflow_chat_id:
        body: Dict[str, Any] = {}
        if payload.model_name:
            body["llm"] = {"model_name": payload.model_name}

        prompt: Dict[str, Any] = {}
        if normalized_prompt is not None:
            prompt["system"] = normalized_prompt
        if payload.top_n is not None:
            prompt["top_n"] = payload.top_n
        if payload.similarity_threshold is not None:
            prompt["similarity_threshold"] = payload.similarity_threshold
        if payload.show_citations is not None:
            # RAGFlow 使用 quote/show_quote 控制引用显示
            prompt["quote"] = bool(payload.show_citations)
        if prompt:
            body["prompt"] = prompt

        await _ragflow_update_chat_settings(conv.ragflow_chat_id, body)

    # 更新本地配置（即使不同步 RAGFlow 也会生效）
    if payload.model_name is not None:
        conv.model_name = payload.model_name
    if normalized_prompt is not None:
        conv.system_prompt = normalized_prompt
    if payload.top_n is not None:
        conv.top_n = payload.top_n
    if payload.similarity_threshold is not None:
        conv.similarity_threshold = payload.similarity_threshold
    if payload.show_citations is not None:
        conv.show_citations = payload.show_citations

    conv.updated_at = datetime.utcnow()
    await session.commit()

    return {
        "conversation_id": conv.id,
        "name": conv.name or f"对话-{conv.id}",
        "model_name": conv.model_name,
        "top_n": conv.top_n,
        "similarity_threshold": float(conv.similarity_threshold),
        "show_citations": conv.show_citations,
        "system_prompt": conv.system_prompt,
        "updated_at": conv.updated_at,
    }


@app.put("/conversations/{conversation_id}/rename")
async def rename_conversation(
    conversation_id: int,
    payload: RenameConversationRequest,
    session: AsyncSession = Depends(get_session),
):
    """重命名对话（可同步到 RAGFlow）。"""
    new_name = (payload.new_name or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="新名称不能为空")

    conv = await _get_conversation_for_owner(
        session, conversation_id, payload.role, payload.user_id
    )

    # 先同步 RAGFlow，避免本地成功但远端失败
    if payload.sync_ragflow and conv.ragflow_chat_id:
        await _ragflow_update_chat_name(conv.ragflow_chat_id, new_name)
        if payload.sync_session and conv.ragflow_session_id:
            await _ragflow_update_session_name(
                conv.ragflow_chat_id, conv.ragflow_session_id, new_name
            )

    conv.name = new_name
    conv.updated_at = datetime.utcnow()
    await session.commit()

    return {
        "conversation_id": conv.id,
        "name": conv.name,
        "updated_at": conv.updated_at,
    }


@app.post("/conversations/{conversation_id}/clear")
async def clear_conversation(
    conversation_id: int,
    payload: ClearConversationRequest,
    session: AsyncSession = Depends(get_session),
):
    """清空对话消息（可重置 RAGFlow 会话）。"""
    conv = await _get_conversation_for_owner(
        session, conversation_id, payload.role, payload.user_id
    )

    # 清空本地消息
    await session.execute(
        delete(models.Message).where(models.Message.conversation_id == conv.id)
    )

    new_session_id = None
    if payload.reset_session:
        if payload.sync_ragflow and conv.ragflow_chat_id:
            # 删除旧会话，避免历史干扰
            if conv.ragflow_session_id:
                await _ragflow_delete_sessions(
                    conv.ragflow_chat_id, [conv.ragflow_session_id]
                )
            # 重新创建会话
            new_session_id = await _ragflow_create_session(
                chat_id=conv.ragflow_chat_id,
                name=conv.name or f"conv-{conv.id}",
                user_id=str(payload.user_id),
            )
            conv.ragflow_session_id = new_session_id
        else:
            # 不同步 RAGFlow：清空后让下次发送消息自动重建会话
            conv.ragflow_session_id = None

    conv.updated_at = datetime.utcnow()
    await session.commit()

    return {
        "conversation_id": conv.id,
        "cleared": True,
        "ragflow_session_id": conv.ragflow_session_id,
        "updated_at": conv.updated_at,
    }


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    payload: DeleteConversationRequest,
    session: AsyncSession = Depends(get_session),
):
    """删除对话（可同步删除 RAGFlow 聊天助手）。"""
    conv = await _get_conversation_for_owner(
        session, conversation_id, payload.role, payload.user_id
    )

    if payload.sync_ragflow and conv.ragflow_chat_id:
        await _ragflow_delete_chats([conv.ragflow_chat_id])

    # 删除本地消息与对话记录
    await session.execute(
        delete(models.Message).where(models.Message.conversation_id == conv.id)
    )
    await session.delete(conv)
    await session.commit()

    return {"conversation_id": conversation_id, "deleted": True}


@app.get("/search/logs")
async def list_search_logs(
    role: str,
    user_id: int,
    class_id: Optional[int] = None,
    class_code: Optional[str] = None,
    kb_id: Optional[int] = None,
    query: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    session: AsyncSession = Depends(get_session),
):
    """搜索日志列表（管理员可看全量，教师/学生仅看自己的）。"""
    role = role.lower().strip()
    if role not in {"student", "teacher", "admin"}:
        raise HTTPException(status_code=400, detail="role 参数不合法")
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(status_code=400, detail="分页参数不合法")

    # 权限校验
    if role == "admin":
        admin = (
            await session.execute(
                select(models.Admin).where(models.Admin.id == user_id)
            )
        ).scalar_one_or_none()
        if not admin or admin.status != 1:
            raise HTTPException(status_code=403, detail="管理员不存在或已停用")
    elif role == "teacher":
        teacher = (
            await session.execute(
                select(models.Teacher).where(models.Teacher.id == user_id)
            )
        ).scalar_one_or_none()
        if not teacher or teacher.status != 1:
            raise HTTPException(status_code=403, detail="教师不存在或已停用")
    else:
        student = (
            await session.execute(
                select(models.Student).where(models.Student.id == user_id)
            )
        ).scalar_one_or_none()
        if not student or student.status != 1:
            raise HTTPException(status_code=403, detail="学生不存在或已停用")

    class_id = await _resolve_class_id(session, class_id, class_code)
    dt_from = _parse_date(date_from, "date_from")
    dt_to = _parse_date(date_to, "date_to")

    base_stmt = (
        select(models.SearchLog, models.KnowledgeBase, models.Class)
        .join(models.KnowledgeBase, models.SearchLog.kb_id == models.KnowledgeBase.id)
        .join(models.Class, models.KnowledgeBase.class_id == models.Class.id)
    )

    filters = []
    if kb_id is not None:
        filters.append(models.SearchLog.kb_id == kb_id)
    if class_id is not None:
        filters.append(models.Class.id == class_id)
    if role == "teacher":
        filters.append(models.SearchLog.user_teacher_id == user_id)
    if role == "student":
        filters.append(models.SearchLog.user_student_id == user_id)
    if query:
        filters.append(models.SearchLog.query.like(f"%{query.strip()}%"))
    if dt_from:
        filters.append(models.SearchLog.created_at >= dt_from)
    if dt_to:
        # 仅传日期时，默认包含当天
        if "T" not in date_to and len(date_to) <= 10:
            filters.append(models.SearchLog.created_at < dt_to + timedelta(days=1))
        else:
            filters.append(models.SearchLog.created_at <= dt_to)

    if filters:
        base_stmt = base_stmt.where(*filters)

    total_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await session.execute(total_stmt)).scalar_one()

    rows = (
        await session.execute(
            base_stmt
            .order_by(models.SearchLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items = []
    for log, kb_row, cls in rows:
        user_role = "teacher" if log.user_teacher_id else "student"
        user_id_value = log.user_teacher_id or log.user_student_id
        items.append(
            {
                "log_id": log.id,
                "query": log.query,
                "result_count": log.result_count,
                "created_at": log.created_at,
                "user_role": user_role,
                "user_id": user_id_value,
                "kb_id": kb_row.id,
                "class_id": cls.id,
                "class_code": cls.class_code,
                "class_name": cls.class_name,
            }
        )

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": items,
    }


@app.get("/search/stats")
async def search_stats(
    role: str,
    user_id: int,
    class_id: Optional[int] = None,
    class_code: Optional[str] = None,
    kb_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    days: int = 30,
    top_n: int = 10,
    session: AsyncSession = Depends(get_session),
):
    """搜索统计（管理员可看全量，教师/学生仅看自己的）。"""
    role = role.lower().strip()
    if role not in {"student", "teacher", "admin"}:
        raise HTTPException(status_code=400, detail="role 参数不合法")
    if top_n < 1 or top_n > 50:
        raise HTTPException(status_code=400, detail="top_n 参数不合法")

    # 权限校验
    if role == "admin":
        admin = (
            await session.execute(
                select(models.Admin).where(models.Admin.id == user_id)
            )
        ).scalar_one_or_none()
        if not admin or admin.status != 1:
            raise HTTPException(status_code=403, detail="管理员不存在或已停用")
    elif role == "teacher":
        teacher = (
            await session.execute(
                select(models.Teacher).where(models.Teacher.id == user_id)
            )
        ).scalar_one_or_none()
        if not teacher or teacher.status != 1:
            raise HTTPException(status_code=403, detail="教师不存在或已停用")
    else:
        student = (
            await session.execute(
                select(models.Student).where(models.Student.id == user_id)
            )
        ).scalar_one_or_none()
        if not student or student.status != 1:
            raise HTTPException(status_code=403, detail="学生不存在或已停用")

    class_id = await _resolve_class_id(session, class_id, class_code)
    dt_from = _parse_date(date_from, "date_from")
    dt_to = _parse_date(date_to, "date_to")

    if not dt_from and not dt_to and days:
        dt_from = datetime.utcnow() - timedelta(days=days)

    filters = []
    if kb_id is not None:
        filters.append(models.SearchLog.kb_id == kb_id)
    if class_id is not None:
        filters.append(models.SearchLog.kb_id.in_(
            select(models.KnowledgeBase.id).where(models.KnowledgeBase.class_id == class_id)
        ))
    if role == "teacher":
        filters.append(models.SearchLog.user_teacher_id == user_id)
    if role == "student":
        filters.append(models.SearchLog.user_student_id == user_id)
    if dt_from:
        filters.append(models.SearchLog.created_at >= dt_from)
    if dt_to:
        if "T" not in date_to and len(date_to) <= 10:
            filters.append(models.SearchLog.created_at < dt_to + timedelta(days=1))
        else:
            filters.append(models.SearchLog.created_at <= dt_to)

    # 总搜索次数
    total_stmt = select(func.count()).select_from(models.SearchLog)
    if filters:
        total_stmt = total_stmt.where(*filters)
    total = (await session.execute(total_stmt)).scalar_one()

    # 独立教师/学生数
    teacher_count_stmt = select(func.count(func.distinct(models.SearchLog.user_teacher_id))).where(
        models.SearchLog.user_teacher_id.isnot(None)
    )
    student_count_stmt = select(func.count(func.distinct(models.SearchLog.user_student_id))).where(
        models.SearchLog.user_student_id.isnot(None)
    )
    if filters:
        teacher_count_stmt = teacher_count_stmt.where(*filters)
        student_count_stmt = student_count_stmt.where(*filters)
    unique_teacher_count = (await session.execute(teacher_count_stmt)).scalar_one()
    unique_student_count = (await session.execute(student_count_stmt)).scalar_one()

    # Top 查询关键词
    top_stmt = (
        select(models.SearchLog.query, func.count().label("cnt"))
        .group_by(models.SearchLog.query)
        .order_by(func.count().desc())
        .limit(top_n)
    )
    if filters:
        top_stmt = top_stmt.where(*filters)
    top_rows = (await session.execute(top_stmt)).all()
    top_queries = [{"query": q, "count": c} for q, c in top_rows]

    # 按天统计（UTC 日期）
    daily_stmt = (
        select(func.date(models.SearchLog.created_at), func.count().label("cnt"))
        .group_by(func.date(models.SearchLog.created_at))
        .order_by(func.date(models.SearchLog.created_at))
    )
    if filters:
        daily_stmt = daily_stmt.where(*filters)
    daily_rows = (await session.execute(daily_stmt)).all()
    daily = [
        {"date": d.isoformat() if d else None, "count": c}
        for d, c in daily_rows
    ]

    return {
        "total_searches": total,
        "unique_teacher_count": unique_teacher_count,
        "unique_student_count": unique_student_count,
        "top_queries": top_queries,
        "daily": daily,
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
