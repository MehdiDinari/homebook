.PHONY: up down logs migrate test lint worker

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api

migrate:
	docker compose exec api alembic upgrade head

test:
	pytest

lint:
	ruff check .

worker:
	docker compose run --rm arq-worker
