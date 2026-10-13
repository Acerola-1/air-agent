#!/usr/bin/env bash
# 只在自动新建的随机隔离库演练；凭据由 TENANT_TEST_OWNER_URI 环境提供。
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/drill_recovery.py "$@"
