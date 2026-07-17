from __future__ import annotations

import asyncio
from pathlib import Path

from app.api.dependencies import get_container
from app.domain.models import Principal


async def main() -> None:
    container = get_container()
    principal = Principal("demo-user", "demo-company", "customer-service")
    for path in sorted(Path("samples/knowledge").glob("*.md")):
        result = await container.ingestion.ingest_path(path, principal, visibility="department")
        print(path.name, result)


if __name__ == "__main__":
    asyncio.run(main())

