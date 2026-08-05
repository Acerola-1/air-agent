#!/bin/bash
# ============================================
# 启动 Air Agent (FastAPI + SQLite)
# 替代 langgraph dev: 自建 server, 完整控制 + SQLite 持久化
# ============================================
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

PORT="${LANGGRAPH_PORT:-8125}"
HOST="${LANGGRAPH_HOST:-0.0.0.0}"

if [ ! -f .env ]; then
    echo "[ERROR] .env 不存在,请先 cp .env.example .env 并填入真实配置" >&2
    exit 1
fi

# 加载环境变量
set -a
. ./.env
set +a

# 检查 venv
if [ ! -x .venv/bin/python ]; then
    echo "[ERROR] .venv/bin/python 不存在,请先运行: uv venv --python 3.13 .venv && uv pip install -r requirements.lock.txt" >&2
    exit 1
fi

# 检查关键依赖
if ! .venv/bin/python -c "import fastapi, uvicorn; from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver" 2>/dev/null; then
    echo "[ERROR] 关键依赖缺失, 请运行: uv add fastapi 'uvicorn[standard]' langgraph-checkpoint-sqlite && uv pip install -r requirements.lock.txt" >&2
    exit 1
fi

# 探活 SQLite checkpointer(确保父目录存在 + 创建表)
if ! .venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
from common.config.checkpointing import get_checkpointer
cp = get_checkpointer()
print('checkpointer OK:', type(cp).__name__)
" 2>&1; then
    echo "[ERROR] SQLite checkpointer 初始化失败,检查 .env 中 SQLITE_PATH" >&2
    exit 1
fi

# 启动 uvicorn
echo "=========================================="
echo "  Air Agent"
echo "  http://${HOST}:${PORT}"
echo "  6 个图: basic-qa / intelligent-analysis / data-analysis /"
echo "          intelligent-report / deep-research / intelligent-tracing"
echo "=========================================="

nohup .venv/bin/uvicorn web.server:app \
    --host "$HOST" \
    --port "$PORT" \
    --no-access-log \
    --app-dir src \
    </dev/null >>/tmp/air-agent.log 2>&1 &

SERVER_PID=$!
disown $SERVER_PID 2>/dev/null || true
echo "  PID=$SERVER_PID  (log: /tmp/air-agent.log)"
exit 0
