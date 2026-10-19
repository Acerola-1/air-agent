#!/usr/bin/env bash
# Compatibility entry: use the single post-I4 launcher, never recreate the full stack.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
if [[ $# -eq 0 ]]; then set -- start; fi
exec "$ROOT/.venv/bin/python" "$ROOT/deployment/run_separated_local.py" "$@"
