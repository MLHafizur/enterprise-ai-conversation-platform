#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

uv lock --check

BACKEND_TESTS=(
  tests/unit/domain/test_code_security.py
  tests/unit/application/test_analyze_code_security.py
  tests/unit/application/test_process_code_scan.py
  tests/unit/infrastructure/code_analysis
  tests/unit/infrastructure/persistence/test_code_security_model.py
  tests/unit/infrastructure/realtime/test_code_scan_publisher.py
  tests/unit/api/test_code_security_api.py
  tests/unit/test_code_security_worker.py
  tests/performance/test_code_security_performance.py
  tests/performance/test_code_security_http_performance.py
)

FRONTEND_TESTS=(
  src/codeSecurity.test.js
  src/components/CodeSecurityAnalysis.test.js
  src/components/CodeSecuritySettings.test.js
  src/components/Dashboard.test.js
  src/components/SafeCodeSnippet.test.js
  src/components/SecurityDashboard.test.js
  src/components/SecurityRepositories.test.js
  src/components/Sidebar.test.js
)

printf 'Running focused Day 10 backend tests with branch coverage...\n'
uv run pytest "${BACKEND_TESTS[@]}" \
  --cov=enterprise_chat_platform.api.code_security \
  --cov=enterprise_chat_platform.application.code_security \
  --cov=enterprise_chat_platform.application.code_security_ai \
  --cov=enterprise_chat_platform.application.process_code_scan \
  --cov=enterprise_chat_platform.code_security_worker \
  --cov=enterprise_chat_platform.domain.code_security \
  --cov=enterprise_chat_platform.infrastructure.code_analysis \
  --cov=enterprise_chat_platform.infrastructure.realtime.code_scan_publisher \
  --cov-report=term-missing \
  --cov-report=html:htmlcov/day10

if [[ ! -x frontend/node_modules/.bin/react-scripts ]]; then
  printf 'error: frontend dependencies are missing; run npm --prefix frontend ci\n' >&2
  exit 1
fi

printf 'Running focused Day 10 frontend tests with coverage...\n'
CI=true npm --prefix frontend run test:coverage -- \
  --runTestsByPath "${FRONTEND_TESTS[@]}"

printf 'Day 10 coverage checks passed. Backend HTML report: htmlcov/day10/index.html\n'
printf 'Frontend report: frontend/coverage/lcov-report/index.html\n'
