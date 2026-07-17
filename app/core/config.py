from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "RAGOps Hub")
    app_env: str = os.getenv("APP_ENV", "development")
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    data_dir: Path = Path(os.getenv("DATA_DIR", "./data"))

    vector_backend: str = os.getenv("VECTOR_BACKEND", "memory")
    milvus_uri: str = os.getenv("MILVUS_URI", "http://localhost:19530")
    milvus_token: str = os.getenv("MILVUS_TOKEN", "")
    milvus_collection: str = os.getenv("MILVUS_COLLECTION", "enterprise_knowledge")

    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "hash")
    embedding_dimension: int = int(os.getenv("EMBEDDING_DIMENSION", "384"))
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    chat_model: str = os.getenv("CHAT_MODEL", "gpt-4.1-mini")
    llm_enabled: bool = _bool("LLM_ENABLED", False)

    top_k_dense: int = int(os.getenv("TOP_K_DENSE", "8"))
    top_k_sparse: int = int(os.getenv("TOP_K_SPARSE", "8"))
    top_k_final: int = int(os.getenv("TOP_K_FINAL", "5"))
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "500"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "80"))
    rrf_k: int = int(os.getenv("RRF_K", "60"))

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "enterprise_rag.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    def prepare_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.prepare_directories()
    return settings
