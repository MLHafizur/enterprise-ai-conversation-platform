#!/usr/bin/env bash

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

read_env_value() {
  local key="$1"
  awk -F= -v key="${key}" '$1 == key { sub(/^[^=]*=/, ""); value=$0 } END { print value }' \
    "${ENV_FILE}"
}

command -v curl >/dev/null 2>&1 || die "curl is required"
[[ -f "${ENV_FILE}" ]] || die "run ./scripts/start-local.sh first"

APP_PORT="$(read_env_value APP_PORT)"
APP_PORT="${APP_PORT:-3000}"
[[ "${APP_PORT}" =~ ^[0-9]+$ ]] || die "APP_PORT in .env is invalid"
ORIGIN="http://127.0.0.1:${APP_PORT}"

curl --fail-with-body --silent --show-error \
  "${ORIGIN}/api/health/live" >/dev/null || die "local stack is not healthy"

LOGIN_RESPONSE="$(curl --fail-with-body --silent --show-error \
  -H 'Content-Type: application/json' \
  --data '{"tenant_id":"tenant_1","role":"admin"}' \
  "${ORIGIN}/api/auth/login")"

ACCESS_TOKEN="$(printf '%s' "${LOGIN_RESPONSE}" | UV_CACHE_DIR=/tmp/day10-demo-uv-cache \
  uv run python -c 'import json,sys; value=json.load(sys.stdin).get("access_token"); assert isinstance(value,str) and value; print(value)')" \
  || die "could not read the local demo token"

SCAN_RESPONSE="$(curl --fail-with-body --silent --show-error \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H 'Content-Type: application/json' \
  --data '{"source_label":"day10-demo.py","code":"import os\npassword = \"hardcoded123\"\nquery = \"SELECT * FROM users WHERE id = \" + user_id\ncursor.execute(query)\nos.system(\"ls \" + user_input)\neval(user_input)"}' \
  "${ORIGIN}/api/v1/security/scans/snippet")"

printf '%s' "${SCAN_RESPONSE}" | UV_CACHE_DIR=/tmp/day10-demo-uv-cache uv run python -c '
import json, sys
scan = json.load(sys.stdin)
expected = {
    "command_injection",
    "hardcoded_secret",
    "sql_injection",
    "unsafe_eval_exec",
}
actual = {finding["rule_id"] for finding in scan["findings"]}
missing = expected - actual
if scan["gate_decision"] != "block" or missing:
    raise SystemExit(
        "demo verification failed: gate={!r}, missing={!r}".format(
            scan["gate_decision"], sorted(missing)
        )
    )
print("Day 10 secure-code demo completed")
print("scan: {}".format(scan["id"]))
print("gate: {}".format(scan["gate_decision"]))
print("findings: {}".format(scan["total_findings"]))
for finding in scan["findings"]:
    print("- {}: {} ({})".format(
        finding["severity"], finding["rule_id"], finding["cwe_id"]
    ))
'

printf 'Open %s and choose Security Dashboard or Code Analysis.\n' "${ORIGIN}"
