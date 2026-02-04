from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.db_url, echo=False, pool_pre_ping=True, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()

aSYNC_DOC = """Use AsyncSessionLocal() as session in request handlers.
Example:
    async with AsyncSessionLocal() as session:
        ...
"""
