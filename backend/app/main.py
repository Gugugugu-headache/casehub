from fastapi import FastAPI,Path,Query,Depends,HttpException
from app.config import get_settings

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

#  TODO: add login登陆
@app.get("/login")
async def login():
    return {"message": "Login successful"}

# TODO: register 注册
@app.post("/register")
async def register():
    return {"message": "Register successful"}