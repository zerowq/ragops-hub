from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

class EmbeddingProvider(Protocol):
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbeddingProvider:
    """Deterministic offline embedding for demos and tests.

    Character n-grams make it usable for both Chinese and English. It is not a
    semantic model, so production profiles should switch to an embedding API.
    """

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension

    def _embed_one(self, text: str) -> list[float]:
        normalized = re.sub(r"\s+", " ", text.lower()).strip()
        features = list(normalized)
        features += [normalized[i : i + 2] for i in range(max(0, len(normalized) - 1))]
        features += re.findall(r"[a-z0-9_\-]+", normalized)
        vector = [0.0] * self.dimension
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]


class OpenAICompatibleEmbeddingProvider:
    def __init__(self, base_url: str, api_key: str, model: str, dimension: int) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = httpx.Timeout(connect=5, read=60, write=30, pool=5)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json={"model": self.model, "input": texts},
            )
            response.raise_for_status()
            payload = response.json()
        ordered = sorted(payload["data"], key=lambda item: item["index"])
        vectors = [item["embedding"] for item in ordered]
        if len(vectors) != len(texts):
            raise ValueError(
                f"Embedding count mismatch: requested={len(texts)}, actual={len(vectors)}"
            )
        invalid_dimensions = {
            len(vector) for vector in vectors if len(vector) != self.dimension
        }
        if invalid_dimensions:
            raise ValueError(
                "Embedding dimension mismatch: "
                f"configured={self.dimension}, actual={sorted(invalid_dimensions)}"
            )
        return vectors
