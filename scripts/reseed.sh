#!/usr/bin/env bash
# Сносит все доменные таблицы в БД и заново наполняет демо-данными
# через программный путь scripts/seed.py:
#   - 5 демо-аккаунтов (test@example.com, manager/hr/pm/employee@worktime.dev)
#   - 30 именных сотрудников + 5 команд
#   - графики, события, метрики, снимки, roadmap
#
# Запускать из корня репозитория на сервере:
#   ./scripts/reseed.sh
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.full.yml}"

cd "$(dirname "$0")/.."

docker compose -f "$COMPOSE_FILE" exec api python -m scripts.seed --reset
