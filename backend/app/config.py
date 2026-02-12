from typing import Optional

from pydantic import AnyUrl
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    db_url: str

    minio_endpoint: AnyUrl
    minio_access_key: str
    minio_secret_key: str
    minio_bucket_pending: str = "pending"
    minio_bucket_kb: str = "knowledge"

    ragflow_base_url: AnyUrl = "http://localhost:8080"
    ragflow_api_key: str
    ragflow_host_header: Optional[str] = None

    class Config:
        env_prefix = ""
        case_sensitive = False
        env_file = ".env"

@lru_cache()
def get_settings() -> Settings:
    return Settings()
