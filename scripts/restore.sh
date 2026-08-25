#!/usr/bin/env sh
set -eu

if [ "${CONFIRM_RESTORE:-}" != "YES" ]; then
  printf 'Refusing restore. Set CONFIRM_RESTORE=YES after checking the backup.\n' >&2
  exit 1
fi

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.production.yml}"
TARGET="${1:-}"
if [ -z "${TARGET}" ] || [ ! -f "${TARGET}/postgres.sql" ] || [ ! -f "${TARGET}/data.tar.gz" ]; then
  printf 'Usage: CONFIRM_RESTORE=YES ./scripts/restore.sh backups/<timestamp>\n' >&2
  exit 1
fi

docker compose -f "${COMPOSE_FILE}" stop api worker
docker compose -f "${COMPOSE_FILE}" exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "${TARGET}/postgres.sql"
docker compose -f "${COMPOSE_FILE}" exec -T api sh -c 'rm -rf /app/data/* && tar -xzf - -C /app' < "${TARGET}/data.tar.gz"
docker compose -f "${COMPOSE_FILE}" up -d api worker
