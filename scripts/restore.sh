#!/usr/bin/env sh
set -eu

umask 077

if [ "${CONFIRM_RESTORE:-}" != "YES" ]; then
  printf 'Refusing restore. Set CONFIRM_RESTORE=YES after checking the backup.\n' >&2
  exit 1
fi

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.production.yml}"
BACKUP_ROOT="${BACKUP_ROOT:-backups}"
TARGET="${1:-}"
if [ -z "${TARGET}" ] || [ ! -f "${TARGET}/postgres.sql" ] || \
   [ ! -f "${TARGET}/data.tar.gz" ] || [ ! -f "${TARGET}/SHA256SUMS" ]; then
  printf 'Usage: CONFIRM_RESTORE=YES ./scripts/restore.sh backups/<timestamp>\n' >&2
  exit 1
fi
if [ ! -f "${COMPOSE_FILE}" ]; then
  printf 'Compose file not found: %s\n' "${COMPOSE_FILE}" >&2
  exit 1
fi

BACKUP_ROOT_ABS="$(cd "${BACKUP_ROOT}" 2>/dev/null && pwd -P)" || {
  printf 'Backup root not found: %s\n' "${BACKUP_ROOT}" >&2
  exit 1
}
TARGET_ABS="$(cd "${TARGET}" 2>/dev/null && pwd -P)" || {
  printf 'Backup target not found: %s\n' "${TARGET}" >&2
  exit 1
}
case "${TARGET_ABS}/" in
  "${BACKUP_ROOT_ABS}/"*) ;;
  *)
    printf 'Refusing restore outside backup root: %s\n' "${TARGET}" >&2
    exit 1
    ;;
esac

(
  cd "${TARGET_ABS}"
  sha256sum -c SHA256SUMS
)

# Always create a fresh recovery point before modifying the database or data
# volume. Configuration is preserved in that backup but is not overwritten by
# this restore operation.
if [ "${SKIP_PRE_RESTORE_BACKUP:-}" != "YES" ]; then
  COMPOSE_FILE="${COMPOSE_FILE}" BACKUP_ROOT="${BACKUP_ROOT}" ./scripts/backup.sh
fi

docker compose -f "${COMPOSE_FILE}" stop api worker
restart_services() {
  docker compose -f "${COMPOSE_FILE}" up -d api worker >/dev/null 2>&1 || true
}
trap restart_services EXIT INT TERM

docker compose -f "${COMPOSE_FILE}" exec -T postgres sh -c \
  'psql -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "${TARGET_ABS}/postgres.sql"

# The API service is stopped, so use a one-off container to mount and restore
# the same named data volume. Refuse to delete unless the resolved target is
# exactly /app/data.
docker compose -f "${COMPOSE_FILE}" run --rm --no-deps -T api sh -c '
  set -eu
  [ "$(readlink -f /app/data)" = "/app/data" ]
  find /app/data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  tar -xzf - -C /app
' < "${TARGET_ABS}/data.tar.gz"

docker compose -f "${COMPOSE_FILE}" up -d api worker
trap - EXIT INT TERM
