.PHONY: install dev test lint ingest docker-up docker-full docker-down

install:
	python3 -m venv .venv
	.venv/bin/pip install -e '.[dev]'

dev:
	.venv/bin/uvicorn app.main:app --reload

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check app tests scripts

ingest:
	.venv/bin/python -m app.cli ingest samples/knowledge/*.md

docker-up:
	docker compose up -d etcd minio milvus

docker-full:
	docker compose --profile full up -d --build

docker-down:
	docker compose --profile full down

