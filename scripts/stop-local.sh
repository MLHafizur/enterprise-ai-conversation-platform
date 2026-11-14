#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"
COMPOSE_FILE="${PROJECT_ROOT}/compose.yaml"
REMOVE_VOLUMES=false

usage() {
  cat <<'EOF'
Usage: ./scripts/stop-local.sh [--volumes]

Stops the local Compose stack. PostgreSQL data is preserved by default.
Pass --volumes to remove the project volumes, including local database data.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

while (($# > 0)); do
  case "$1" in
    --volumes)
      REMOVE_VOLUMES=true
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown argument: $1"
      ;;
  esac
  shift
done

command -v docker >/dev/null 2>&1 || die "Docker is required"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
docker info >/dev/null 2>&1 || die "the Docker daemon is not available"
[[ -f "${ENV_FILE}" ]] || \
  die "missing .env; cannot reliably identify the local Compose project"
[[ -f "${COMPOSE_FILE}" ]] || die "missing ${COMPOSE_FILE}"

compose=(docker compose --env-file "${ENV_FILE}" --file "${COMPOSE_FILE}")
down_options=(--remove-orphans --timeout 30)

if [[ "${REMOVE_VOLUMES}" == "true" ]]; then
  printf 'Stopping the stack and removing its local volumes...\n'
  down_options+=(--volumes)
else
  printf 'Stopping the stack; PostgreSQL data will be preserved...\n'
fi

"${compose[@]}" down "${down_options[@]}"

if [[ "${REMOVE_VOLUMES}" == "true" ]]; then
  printf 'The stack and its local database volume were removed.\n'
else
  printf 'The stack is stopped. Run ./scripts/start-local.sh to restart it.\n'
fi
