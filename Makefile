.PHONY: install lint format typecheck test retrieval-bench build audit supply-chain docker-build run db-migrate db-current db-backup db-restore db-verify-restore

BACKUP_FILE ?= build/backups/ukg.dump
BACKUP_MANIFEST ?= build/backups/ukg.manifest.json
IMAGE_TAG ?= local

install:
	python -m pip install --upgrade pip setuptools==84.0.0 wheel==0.48.0
	python -m pip install -e '.[dev]'

lint:
	ruff check .

format:
	ruff format .
	ruff check . --fix

typecheck:
	mypy src

test:
	pytest

retrieval-bench:
	python scripts/retrieval_quality_bench.py

build:
	python -m pip install build==1.2.2.post1
	python -m build

audit:
	pip-audit --skip-editable

supply-chain:
	python scripts/validate_supply_chain.py

docker-build:
	docker build -t universal-ai-knowledge-graph:$(IMAGE_TAG) .

db-migrate:
	alembic upgrade head

db-current:
	alembic current

db-backup:
	python scripts/postgres_dr.py backup --dump "$(BACKUP_FILE)" --manifest "$(BACKUP_MANIFEST)"

db-restore:
	python scripts/postgres_dr.py restore --dump "$(BACKUP_FILE)" --manifest "$(BACKUP_MANIFEST)" --allow-destructive-restore

db-verify-restore:
	python scripts/postgres_dr.py verify --dump "$(BACKUP_FILE)" --manifest "$(BACKUP_MANIFEST)"

run:
	uvicorn universal_kg.api.main:app --reload
