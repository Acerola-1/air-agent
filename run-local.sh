#!/bin/bash
# ============================================
# 启动 Air Agent (langgraph dev + 静态前端)
# 替代 run.sh: 标准化 langgraph-api 协议, 前后端分进程
# ============================================
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# 端口配置: langgraph-api 2024, 静态前端 8125
LANGGRAPH_PORT="${LANGGRAPH_PORT:-2024}"
STATIC_PORT="${STATIC_PORT:-8125}"
LANGGRAPH_HOST="${LANGGRAPH_HOST:-0.0.0.0}"
STATIC_HOST="${STATIC_HOST:-0.0.0.0}"

LANGGRAPH_PID_FILE="/tmp/air-agent-langgraph.pid"
LANGGRAPH_LOG_FILE="/tmp/air-agent-langgraph.log"
STATIC_PID_FILE="/tmp/air-agent-static.pid"
STATIC_LOG_FILE="/tmp/air-agent-static.log"

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
if ! .venv/bin/python -c "import langgraph_api, langgraph_cli, langgraph_runtime_inmem; from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver" 2>/dev/null; then
  echo "[ERROR] 关键依赖缺失, 请运行: uv pip install -r requirements.lock.txt" >&2
  exit 1
fi

# 检查 langgraph dev 二进制
if ! .venv/bin/langgraph --version >/dev/null 2>&1; then
  echo "[ERROR] langgraph CLI 不可用, 请检查 venv 安装" >&2
  exit 1
fi

# 探活 SQLite checkpointer(确保父目录存在 + 创建表) — 保留作为可选本地持久化后端
if ! .venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
from common.config.checkpointing import get_checkpointer
cp = get_checkpointer()
print('checkpointer OK:', type(cp).__name__)
" 2>&1; then
  echo "[WARN] SQLite checkpointer 初始化失败, 继续 (langgraph dev 自带 in-memory 后端)" >&2
fi

# 启动 langgraph dev (后台)
echo "=========================================="
echo "  Air Agent (langgraph-api 标准化模式)"
echo "  后端 langgraph-api:  http://${LANGGRAPH_HOST}:${LANGGRAPH_PORT}"
echo "  前端静态:            http://${STATIC_HOST}:${STATIC_PORT}"
echo "  6 个图: basic-qa / intelligent-analysis / data-analysis /"
echo "         intelligent-report / deep-research / intelligent-tracing"
echo "=========================================="

# 启动静态文件服务器 (后台)
nohup .venv/bin/python scripts/serve_static.py \
  --port "$STATIC_PORT" \
  --host "$STATIC_HOST" \
  > "$STATIC_LOG_FILE" 2>&1 &
STATIC_PID=$!
echo "$STATIC_PID" > "$STATIC_PID_FILE"
disown "$STATIC_PID" 2>/dev/null || true
echo "  静态前端 PID=$STATIC_PID  (log: $STATIC_LOG_FILE)"

# 启动 langgraph dev (后台)
nohup .venv/bin/langgraph dev \
  --config ./langgraph.json \
  --no-browser \
  --port "$LANGGRAPH_PORT" \
  --host "$LANGGRAPH_HOST" \
  > "$LANGGRAPH_LOG_FILE" 2>&1 &
LANGGRAPH_PID=$!
echo "$LANGGRAPH_PID" > "$LANGGRAPH_PID_FILE"
disown "$LANGGRAPH_PID" 2>/dev/null || true
echo "  langgraph-api PID=$LANGGRAPH_PID  (log: $LANGGRAPH_LOG_FILE)"

# Readiness probe: 等待 langgraph-api 加载完 6 个图
echo "  等待 langgraph-api 就绪..."
for i in {1..60}; do
  if curl -sf -X POST "http://localhost:${LANGGRAPH_PORT}/assistants/search" \
       -H "Content-Type: application/json" -d '{}' >/dev/null 2>&1; then
    COUNT=$(curl -s -X POST "http://localhost:${LANGGRAPH_PORT}/assistants/search" \
            -H "Content-Type: application/json" -d '{}' | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null)
    if [ "$COUNT" = "6" ]; then
      echo "  ✓ 就绪: $COUNT 个 assistant 已注册"
      break
    fi
  fi
  sleep 1
done

echo "  浏览器打开: http://localhost:${STATIC_PORT}"
exit 0
