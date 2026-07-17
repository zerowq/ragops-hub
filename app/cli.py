from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.api.dependencies import get_container
from app.domain.models import Principal


async def ingest(paths: list[str], tenant: str, department: str, visibility: str) -> None:
    container = get_container()
    principal = Principal("cli-user", tenant, department)
    for value in paths:
        result = await container.ingestion.ingest_path(Path(value), principal, visibility=visibility)
        print(value, result)


def main() -> None:
    parser = argparse.ArgumentParser(description="RAGOps Hub CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest_parser = subparsers.add_parser("ingest")
    ingest_parser.add_argument("paths", nargs="+")
    ingest_parser.add_argument("--tenant", default="demo-company")
    ingest_parser.add_argument("--department", default="customer-service")
    ingest_parser.add_argument("--visibility", default="department")
    args = parser.parse_args()
    if args.command == "ingest":
        asyncio.run(ingest(args.paths, args.tenant, args.department, args.visibility))


if __name__ == "__main__":
    main()
