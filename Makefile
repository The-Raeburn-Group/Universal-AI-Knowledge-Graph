.PHONY: install lint format typecheck test build docker-build run db-migrate db-current db-backup db-restore db-verify-restore

BACKUP_FILE ?= build/backups/ukg.dump
BACKUP_MANIFEST ?= build/backups/ukg.manifest.json

install:
	python -m pip install --upgrade pip
	pip install -e '.[dev]'

lint:
	ruff check .

format:
	ruff format .
	ruff check . --fix

typecheck:
	mypy src

test:
	pytest

build:
	python -m pip install build==1.2.2.post1
	python -m build

docker-build:
	docker build -t universal-ai-knowledge-graph:local .

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
