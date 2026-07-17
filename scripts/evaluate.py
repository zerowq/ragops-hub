from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from app.api.dependencies import get_container
from app.domain.models import Principal


async def main() -> None:
    parser = argparse.ArgumentParser(description="Offline retrieval evaluation")
    parser.add_argument("--dataset", default="samples/eval/questions.jsonl")
    parser.add_argument("--tenant", default="demo-company")
    parser.add_argument("--department", default="customer-service")
    args = parser.parse_args()

    container = get_container()
    principal = Principal("eval-user", args.tenant, args.department)
    questions = [json.loads(line) for line in Path(args.dataset).read_text().splitlines() if line]
    source_hits = 0
    reciprocal_rank_sum = 0.0
    latencies: list[float] = []
    details: list[dict[str, object]] = []
    for question in questions:
        started = time.perf_counter()
        hits = await container.retriever.search(question["query"], principal)
        latencies.append((time.perf_counter() - started) * 1000)
        sources = [hit.chunk.source for hit in hits]
        expected = question["expected_source"]
        rank = sources.index(expected) + 1 if expected in sources else None
        if rank:
            source_hits += 1
            reciprocal_rank_sum += 1 / rank
        details.append({"id": question["id"], "expected": expected, "rank": rank, "sources": sources})

    count = max(1, len(questions))
    result = {
        "questions": len(questions),
        "source_recall_at_k": round(source_hits / count, 4),
        "mrr": round(reciprocal_rank_sum / count, 4),
        "average_retrieval_ms": round(sum(latencies) / count, 2),
        "details": details,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

