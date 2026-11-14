#!/usr/bin/env bash

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_TEMPLATE="${PROJECT_ROOT}/.env.example"
ENV_FILE="${PROJECT_ROOT}/.env"
COMPOSE_FILE="${PROJECT_ROOT}/compose.yaml"
TEMP_ENV_FILE=""

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [[ -n "${TEMP_ENV_FILE}" && -f "${TEMP_ENV_FILE}" ]]; then
    rm -f -- "${TEMP_ENV_FILE}"
  fi
}

trap cleanup EXIT

resolve_env_file() {
  local candidate="${ENV_FILE}"
  local candidate_directory
  local link_target
  local hop_count=0

  while [[ -L "${candidate}" ]]; do
    ((hop_count += 1))
    ((hop_count <= 16)) || die "too many .env symlink hops"
    link_target="$(readlink "${candidate}")"
    if [[ "${link_target}" == /* ]]; then
      candidate="${link_target}"
    else
      candidate_directory="$(cd -- "$(dirname -- "${candidate}")" && pwd -P)"
      candidate="${candidate_directory}/${link_target}"
    fi
  done

  ENV_FILE="${candidate}"
}

read_env_value() {
  local key="$1"

  awk -v key="${key}" '
    function assignment_key(line, normalized) {
      normalized = line
      sub(/^[[:space:]]*/, "", normalized)
      sub(/^export[[:space:]]+/, "", normalized)
      if (normalized !~ /^[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=/) {
        return ""
      }
      sub(/[[:space:]]*=.*/, "", normalized)
      return normalized
    }
    assignment_key($0) == key {
      value = $0
      sub(/^[[:space:]]*/, "", value)
      sub(/^export[[:space:]]+/, "", value)
      sub(/^[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=[[:space:]]*/, "", value)
    }
    END { print value }
  ' "${ENV_FILE}"
}

has_env_key() {
  local key="$1"

  awk -v key="${key}" '
    function assignment_key(line, normalized) {
      normalized = line
      sub(/^[[:space:]]*/, "", normalized)
      sub(/^export[[:space:]]+/, "", normalized)
      if (normalized !~ /^[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=/) {
        return ""
      }
      sub(/[[:space:]]*=.*/, "", normalized)
      return normalized
    }
    assignment_key($0) == key { found = 1; exit }
    END { exit !found }
  ' "${ENV_FILE}"
}

is_template_secret_key() {
  local key="$1"

  case "${key}" in
    *_PASSWORD | *_KEY | *_TOKEN | *_SECRET | CHAT_DATABASE_URL | CHAT_MIGRATION_DATABASE_URL)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

add_missing_non_secret_defaults() {
  local line
  local key
  local -a missing_defaults=()

  while IFS= read -r line || [[ -n "${line}" ]]; do
    if [[ ! "${line}" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]]; then
      continue
    fi

    key="${BASH_REMATCH[1]}"
    if is_template_secret_key "${key}" || has_env_key "${key}"; then
      continue
    fi
    missing_defaults+=("${line}")
  done <"${ENV_TEMPLATE}"

  if ((${#missing_defaults[@]} == 0)); then
    return
  fi

  TEMP_ENV_FILE="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
  awk '{ print }' "${ENV_FILE}" >"${TEMP_ENV_FILE}"
  {
    printf '\n# Local defaults added from .env.example by start-local.sh\n'
    printf '%s\n' "${missing_defaults[@]}"
  } >>"${TEMP_ENV_FILE}"
  chmod 600 "${TEMP_ENV_FILE}"
  mv -- "${TEMP_ENV_FILE}" "${ENV_FILE}"
  TEMP_ENV_FILE=""
  printf 'Added %s missing non-secret setting(s) to .env.\n' "${#missing_defaults[@]}"
}

set_env_value() {
  local key="$1"
  local value="$2"

  TEMP_ENV_FILE="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
  awk -v key="${key}" -v value="${value}" '
    function assignment_key(line, normalized) {
      normalized = line
      sub(/^[[:space:]]*/, "", normalized)
      sub(/^export[[:space:]]+/, "", normalized)
      if (normalized !~ /^[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=/) {
        return ""
      }
      sub(/[[:space:]]*=.*/, "", normalized)
      return normalized
    }
    {
      lines[NR] = $0
      if (assignment_key($0) == key) {
        target_line = NR
      }
    }
    END {
      for (line_number = 1; line_number <= NR; line_number++) {
        if (line_number == target_line) {
          print key "=" value
        } else {
          print lines[line_number]
        }
      }
      if (!target_line) {
        print key "=" value
      }
    }
  ' "${ENV_FILE}" >"${TEMP_ENV_FILE}"
  chmod 600 "${TEMP_ENV_FILE}"
  mv -- "${TEMP_ENV_FILE}" "${ENV_FILE}"
  TEMP_ENV_FILE=""
}

ensure_secret() {
  local key="$1"
  local byte_count="$2"
  local value

  value="$(read_env_value "${key}")"
  if [[ -n "${value//[[:space:]]/}" ]]; then
    return
  fi

  command -v openssl >/dev/null 2>&1 || \
    die "${key} is blank and openssl is unavailable; set it manually in .env"

  value="$(openssl rand -hex "${byte_count}")"
  [[ -n "${value}" ]] || die "could not generate ${key}"
  set_env_value "${key}" "${value}"
  printf 'Generated local %s in .env.\n' "${key}"
}

validate_positive_integer() {
  local label="$1"
  local value="$2"
  local maximum="$3"

  [[ "${value}" =~ ^[0-9]+$ ]] || die "${label} must be a positive integer"
  ((value >= 1 && value <= maximum)) || \
    die "${label} must be between 1 and ${maximum}"
}

main() {
  local chat_environment
  local app_port
  local start_timeout
  local frontend_container
  local -a compose

  command -v docker >/dev/null 2>&1 || die "Docker is required"
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
  docker info >/dev/null 2>&1 || die "the Docker daemon is not available"

  [[ -f "${ENV_TEMPLATE}" ]] || die "missing ${ENV_TEMPLATE}"
  [[ -f "${COMPOSE_FILE}" ]] || die "missing ${COMPOSE_FILE}"

  resolve_env_file
  if [[ ! -f "${ENV_FILE}" ]]; then
    cp -- "${ENV_TEMPLATE}" "${ENV_FILE}"
    printf 'Created .env from .env.example.\n'
  fi
  chmod 600 "${ENV_FILE}"

  add_missing_non_secret_defaults
  ensure_secret POSTGRES_BOOTSTRAP_PASSWORD 24
  ensure_secret POSTGRES_MIGRATOR_PASSWORD 24
  ensure_secret POSTGRES_RUNTIME_PASSWORD 24
  ensure_secret CHAT_JWT_KEY 32

  chat_environment="$(read_env_value CHAT_ENVIRONMENT)"
  [[ "${chat_environment:-local}" == "local" ]] || \
    die "this helper only starts CHAT_ENVIRONMENT=local"

  if [[ "$(read_env_value CHAT_PROVIDER_MODE)" == "gemini" && \
    -z "$(read_env_value CHAT_GEMINI_API_KEY)" ]]; then
    die "CHAT_GEMINI_API_KEY is required when CHAT_PROVIDER_MODE=gemini"
  fi

  app_port="$(read_env_value APP_PORT)"
  app_port="${app_port:-3000}"
  start_timeout="$(read_env_value LOCAL_START_TIMEOUT_SECONDS)"
  start_timeout="${start_timeout:-180}"
  validate_positive_integer APP_PORT "${app_port}" 65535
  validate_positive_integer LOCAL_START_TIMEOUT_SECONDS "${start_timeout}" 3600

  # This helper always launches the bundled local login and realtime stack.
  # Process-scoped overrides keep legacy or custom .env values intact.
  compose=(
    env
    CHAT_AUTH_MODE=jwt
    CHAT_ALLOW_INSECURE_DEVELOPMENT_AUTH=false
    CHAT_DEMO_AUTH_ENABLED=true
    CHAT_JWT_ALGORITHM=HS256
    CHAT_REALTIME_ENABLED=true
    docker compose
    --env-file "${ENV_FILE}"
    --file "${COMPOSE_FILE}"
  )
  "${compose[@]}" config --quiet

  frontend_container="$("${compose[@]}" ps --quiet frontend 2>/dev/null || true)"
  if [[ -z "${frontend_container}" ]] && command -v lsof >/dev/null 2>&1 && \
    lsof -nP -iTCP:"${app_port}" -sTCP:LISTEN >/dev/null 2>&1; then
    die "localhost:${app_port} is already in use; update APP_PORT in .env"
  fi

  printf 'Building and starting with local JWT and realtime settings...\n'
  if ! "${compose[@]}" up \
    --detach \
    --build \
    --remove-orphans \
    --wait \
    --wait-timeout "${start_timeout}"; then
    printf 'The stack did not become healthy. Current service status:\n' >&2
    "${compose[@]}" ps >&2 || true
    exit 1
  fi

  printf '\nLocal chat is ready at http://localhost:%s\n' "${app_port}"
  printf 'API readiness: http://localhost:%s/api/health/ready\n' "${app_port}"
  printf 'API docs:      http://localhost:%s/docs\n' "${app_port}"
  printf 'Stop safely:   ./scripts/stop-local.sh\n'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
