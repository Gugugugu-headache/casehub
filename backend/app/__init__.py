"""FastAPI app package."""
from app.db import Base  # re-export for Alembic
from app import models  # ensure models are imported for metadata
