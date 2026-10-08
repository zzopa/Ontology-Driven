"""Shared management store and per-request model selection."""
from functools import lru_cache
from app.config import settings
from app.admin.store import AdminStore


@lru_cache(maxsize=1)
def get_store():
    return AdminStore(settings.admin_directory, {
        'url': settings.llm_url, 'model': settings.llm_model, 'key': settings.llm_key,
        'retention_days': settings.retention_days,
    })
