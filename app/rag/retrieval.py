from __future__ import annotations

import math
from collections import Counter, defaultdict

from app.domain.models import Chunk, Principal, SearchHit
from app.embeddings.providers import EmbeddingProvider
from app.rag.tokenization import tokenize
from app.storage.repository import SQLiteRepository
from app.storage.vector_store import VectorStore


class BM25Retriever:
    def search(self, query: str, chunks: list[Chunk], limit: int) -> list[SearchHit]:
        if not chunks:
            return []
        documents = [tokenize(chunk.content) for chunk in chunks]
        query_tokens = tokenize(query)
        document_frequency: Counter[str] = Counter()
        for document in documents:
            document_frequency.update(set(document))
        average_length = sum(len(document) for document in documents) / len(documents) or 1.0
        k1, b = 1.5, 0.75
        scores: list[tuple[float, Chunk]] = []
        for chunk, document in zip(chunks, documents, strict=True):
            frequencies = Counter(document)
            score = 0.0
            for token in query_tokens:
                frequency = frequencies[token]
                if not frequency:
                    continue
                df = document_frequency[token]
                idf = math.log(1 + (len(documents) - df + 0.5) / (df + 0.5))
                numerator = frequency * (k1 + 1)
                denominator = frequency + k1 * (1 - b + b * len(document) / average_length)
                score += idf * numerator / denominator
            if score > 0:
                scores.append((score, chunk))
        scores.sort(key=lambda item: item[0], reverse=True)
        return [
            SearchHit(chunk=chunk, score=score, sparse_rank=rank)
            for rank, (score, chunk) in enumerate(scores[:limit], start=1)
        ]


class HybridRetriever:
    def __init__(
        self,
        repository: SQLiteRepository,
        vector_store: VectorStore,
        embedder: EmbeddingProvider,
        top_k_dense: int = 8,
        top_k_sparse: int = 8,
        top_k_final: int = 5,
        rrf_k: int = 60,
        min_dense_score: float = 0.15,
        min_lexical_overlap: float = 0.15,
    ) -> None:
        self.repository = repository
        self.vector_store = vector_store
        self.embedder = embedder
        self.bm25 = BM25Retriever()
        self.top_k_dense = top_k_dense
        self.top_k_sparse = top_k_sparse
        self.top_k_final = top_k_final
        self.rrf_k = rrf_k
        self.min_dense_score = min_dense_score
        self.min_lexical_overlap = min_lexical_overlap

    async def search(self, query: str, principal: Principal) -> list[SearchHit]:
        query_vector = (await self.embedder.embed([query]))[0]
        dense_hits = await self.vector_store.search(query_vector, principal, self.top_k_dense)
        accessible_dense_ids = self.repository.filter_accessible_chunk_ids(
            principal, [hit.chunk.id for hit in dense_hits]
        )
        dense_hits = [hit for hit in dense_hits if hit.chunk.id in accessible_dense_ids]
        sparse_hits = self.repository.search_sparse(query, principal, self.top_k_sparse)
        if sparse_hits is None:
            accessible_chunks = self.repository.list_accessible_chunks(principal)
            sparse_hits = self.bm25.search(query, accessible_chunks, self.top_k_sparse)

        fused_scores: dict[str, float] = defaultdict(float)
        sparse_scores = {hit.chunk.id: hit.score for hit in sparse_hits}
        max_sparse_score = max(sparse_scores.values(), default=1.0)
        candidates: dict[str, SearchHit] = {}
        for hit in dense_hits:
            fused_scores[hit.chunk.id] += 1 / (self.rrf_k + (hit.dense_rank or self.top_k_dense))
            candidates[hit.chunk.id] = hit
        for hit in sparse_hits:
            fused_scores[hit.chunk.id] += 1 / (self.rrf_k + (hit.sparse_rank or self.top_k_sparse))
            if hit.chunk.id in candidates:
                candidates[hit.chunk.id].sparse_rank = hit.sparse_rank
            else:
                candidates[hit.chunk.id] = hit

        query_terms = set(tokenize(query))
        results: list[SearchHit] = []
        for chunk_id, hit in candidates.items():
            chunk_terms = set(tokenize(hit.chunk.content))
            lexical_overlap = len(query_terms & chunk_terms) / max(1, len(query_terms))
            # RRF rewards candidates present in both lists, but with a short corpus it can
            # otherwise bury an exact BM25 match that dense retrieval misses. Keep the
            # rank fusion and add a bounded, score-aware sparse signal for the light rerank.
            normalized_sparse_score = sparse_scores.get(chunk_id, 0.0) / max_sparse_score
            dense_score = hit.dense_score if hit.dense_score is not None else -1.0
            if (
                dense_score < self.min_dense_score
                and lexical_overlap < self.min_lexical_overlap
            ):
                continue
            hit.rerank_score = (
                fused_scores[chunk_id]
                + lexical_overlap * 0.02
                + normalized_sparse_score * 0.04
            )
            hit.score = fused_scores[chunk_id]
            results.append(hit)
        results.sort(key=lambda item: item.rerank_score or 0.0, reverse=True)
        return results[: self.top_k_final]
