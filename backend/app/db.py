from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.config import get_settings

settings = get_settings()

# MySQL 连接显式使用 utf8mb4，避免中文乱码
connect_args = {}
if settings.db_url.lower().startswith("mysql"):
    connect_args["charset"] = "utf8mb4"

engine = create_async_engine(
    settings.db_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
    connect_args=connect_args,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()

aSYNC_DOC = """Use AsyncSessionLocal() as session in request handlers.
Example:
    async with AsyncSessionLocal() as session:
        ...
"""


async def get_session() -> AsyncSession:
    """FastAPI 依赖：提供异步数据库会话。"""
    async with AsyncSessionLocal() as session:
        yield session
