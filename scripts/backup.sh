#!/usr/bin/env sh
set -eu

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.production.yml}"
BACKUP_ROOT="${BACKUP_ROOT:-backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="${BACKUP_ROOT}/${STAMP}"

mkdir -p "${TARGET}"
docker compose -f "${COMPOSE_FILE}" exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=plain' > "${TARGET}/postgres.sql"
docker compose -f "${COMPOSE_FILE}" exec -T api tar -czf - -C /app data > "${TARGET}/data.tar.gz"
printf 'Backup written to %s\n' "${TARGET}"
