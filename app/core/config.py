from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = field(default_factory=lambda: os.getenv("APP_NAME", "RAGOps Hub"))
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    app_host: str = field(default_factory=lambda: os.getenv("APP_HOST", "0.0.0.0"))
    app_port: int = field(default_factory=lambda: int(os.getenv("APP_PORT", "8000")))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "./data")))
    max_upload_bytes: int = field(
        default_factory=lambda: int(
            os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))
        )
    )

    auth_mode: str = field(default_factory=lambda: os.getenv("AUTH_MODE", "demo"))
    jwt_secret: str = field(default_factory=lambda: os.getenv("JWT_SECRET", ""))
    jwt_algorithm: str = field(default_factory=lambda: os.getenv("JWT_ALGORITHM", "HS256"))

    vector_backend: str = field(
        default_factory=lambda: os.getenv("VECTOR_BACKEND", "memory")
    )
    milvus_uri: str = field(
        default_factory=lambda: os.getenv("MILVUS_URI", "http://localhost:19530")
    )
    milvus_token: str = field(default_factory=lambda: os.getenv("MILVUS_TOKEN", ""))
    milvus_collection: str = field(
        default_factory=lambda: os.getenv("MILVUS_COLLECTION", "ragops_knowledge_v1")
    )

    embedding_provider: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_PROVIDER", "hash")
    )
    embedding_dimension: int = field(
        default_factory=lambda: int(os.getenv("EMBEDDING_DIMENSION", "384"))
    )
    openai_base_url: str = field(
        default_factory=lambda: os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
    )
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    embedding_model: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    )
    chat_model: str = field(
        default_factory=lambda: os.getenv("CHAT_MODEL", "gpt-4.1-mini")
    )
    llm_enabled: bool = field(default_factory=lambda: _bool("LLM_ENABLED", False))

    top_k_dense: int = field(
        default_factory=lambda: int(os.getenv("TOP_K_DENSE", "8"))
    )
    top_k_sparse: int = field(
        default_factory=lambda: int(os.getenv("TOP_K_SPARSE", "8"))
    )
    top_k_final: int = field(
        default_factory=lambda: int(os.getenv("TOP_K_FINAL", "5"))
    )
    min_dense_score: float = field(
        default_factory=lambda: float(os.getenv("MIN_DENSE_SCORE", "0.15"))
    )
    min_lexical_overlap: float = field(
        default_factory=lambda: float(os.getenv("MIN_LEXICAL_OVERLAP", "0.15"))
    )
    chunk_size: int = field(
        default_factory=lambda: int(os.getenv("CHUNK_SIZE", "500"))
    )
    chunk_overlap: int = field(
        default_factory=lambda: int(os.getenv("CHUNK_OVERLAP", "80"))
    )
    rrf_k: int = field(default_factory=lambda: int(os.getenv("RRF_K", "60")))

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
