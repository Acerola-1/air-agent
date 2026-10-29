#!/usr/bin/env bash
# Compatibility entry: use the single post-I4 launcher, never recreate the full stack.
# 默认 up：先起依赖容器与 Langfuse，再起 IAM/RAG/Agent。只想拉起宿主进程用 `start`。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
if [[ $# -eq 0 ]]; then set -- up; fi
exec "$ROOT/.venv/bin/python" "$ROOT/deployment/run_separated_local.py" "$@"
