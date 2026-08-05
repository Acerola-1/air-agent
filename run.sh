#!/bin/bash
# ============================================
# 本地启动 LangGraph dev server
# 替代原 Docker 启动方式(无需容器)
# ============================================
set -e

# 工作目录
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# 端口(与 Java 客户端 agent.url 期望一致)
PORT="${LANGGRAPH_PORT:-8125}"
HOST="${LANGGRAPH_HOST:-0.0.0.0}"

# 检查 .env
if [ ! -f .env ]; then
    echo "[ERROR] .env 不存在,请先 cp .env.example .env 并填入真实配置" >&2
    exit 1
fi

# 加载环境变量
set -a
. ./.env
set +a

# 验证 Python venv
if [ ! -x .venv/bin/python ]; then
    echo "[ERROR] .venv/bin/python 不存在,请先运行: uv venv --python 3.13 .venv && uv pip install -r requirements.lock.txt && uv add pymysql langgraph-checkpoint-mysql[pymysql]" >&2
    exit 1
fi

# 验证关键依赖
if ! .venv/bin/python -c "from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver" 2>/dev/null; then
    echo "[ERROR] langgraph-checkpoint-mysql 未安装,请运行: uv add 'langgraph-checkpoint-mysql[pymysql]'" >&2
    exit 1
fi

# 探活 MySQL checkpointer(在启动前先验证,失败立刻退出)
if ! .venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
from common.config.checkpointing import get_checkpointer
get_checkpointer()
print('checkpointer OK')
" 2>&1; then
    echo "[ERROR] MySQL checkpointer 初始化失败,检查 .env 中 MYSQL_URI 是否正确" >&2
    exit 1
fi

echo "=========================================="
echo "  LangGraph dev server"
echo "  http://${HOST}:${PORT}"
echo "  graphs: $(.venv/bin/python -c "import json; print(', '.join(json.load(open('langgraph.json'))['graphs'].keys()))")"
echo "=========================================="

nohup .venv/bin/langgraph dev \
    --config langgraph.json \
    --host "$HOST" \
    --port "$PORT" \
    --no-reload \
    --no-browser \
    </dev/null >>/tmp/langgraph-dev.log 2>&1 &

SERVER_PID=$!
disown $SERVER_PID 2>/dev/null || true
echo "  PID=$SERVER_PID  (log: /tmp/langgraph-dev.log)"
