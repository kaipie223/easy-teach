#!/usr/bin/env sh
set -eu

# Backups contain credentials and user content. New files must never be
# group/world-readable, even when the project directory has loose permissions.
umask 077

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.production.yml}"
BACKUP_ROOT="${BACKUP_ROOT:-backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="${BACKUP_ROOT}/${STAMP}"
WORKING_TARGET="${BACKUP_ROOT}/.${STAMP}.incomplete"

if [ ! -f "${COMPOSE_FILE}" ]; then
  printf 'Compose file not found: %s\n' "${COMPOSE_FILE}" >&2
  exit 1
fi
if [ ! -f .env ] || [ ! -d secrets ]; then
  printf 'Refusing incomplete backup: .env and secrets/ are required.\n' >&2
  exit 1
fi
if [ -e "${TARGET}" ] || [ -e "${WORKING_TARGET}" ]; then
  printf 'Backup target already exists: %s\n' "${TARGET}" >&2
  exit 1
fi

mkdir -p "${BACKUP_ROOT}"
chmod 700 "${BACKUP_ROOT}"
mkdir "${WORKING_TARGET}"
chmod 700 "${WORKING_TARGET}"

docker compose -f "${COMPOSE_FILE}" exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=plain --clean --if-exists --no-owner --no-privileges' \
  > "${WORKING_TARGET}/postgres.sql"
docker compose -f "${COMPOSE_FILE}" exec -T api tar -czf - -C /app data \
  > "${WORKING_TARGET}/data.tar.gz"

# Preserve deployment configuration for rollback without ever printing secret
# contents. This archive intentionally stays outside the application image.
tar -czf "${WORKING_TARGET}/deployment-config.tar.gz" \
  .env secrets "${COMPOSE_FILE}" deploy scripts

(
  cd "${WORKING_TARGET}"
  sha256sum postgres.sql data.tar.gz deployment-config.tar.gz > SHA256SUMS
  chmod 600 postgres.sql data.tar.gz deployment-config.tar.gz SHA256SUMS
)

mv "${WORKING_TARGET}" "${TARGET}"
printf 'Backup written to %s\n' "${TARGET}"
