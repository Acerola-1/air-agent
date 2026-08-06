#!/bin/bash
# ============================================
# 启动 Air Agent (langgraph dev + Next.js assistant-ui 前端)
# 架构:
#   - 后端: langgraph-cli dev 暴露标准 REST API :2024
#   - 前端: Next.js (assistant-ui/react + react-langgraph) :3000
#     前端所有 /api/* 请求通过 Next Route Handler 代理到 :2024,
#     彻底解决 CORS 问题, 浏览器始终同源访问 :3000
# ============================================
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# 端口配置: langgraph-api 2024, Next.js 前端 3000
LANGGRAPH_PORT="${LANGGRAPH_PORT:-2024}"
NEXT_PORT="${NEXT_PORT:-3000}"
LANGGRAPH_HOST="${LANGGRAPH_HOST:-0.0.0.0}"
NEXT_HOST="${NEXT_HOST:-0.0.0.0}"

LANGGRAPH_PID_FILE="/tmp/air-agent-langgraph.pid"
LANGGRAPH_LOG_FILE="/tmp/air-agent-langgraph.log"
NEXT_PID_FILE="/tmp/air-agent-next.pid"
NEXT_LOG_FILE="/tmp/air-agent-next.log"

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

# 检查 Node / npm (assistant-ui 前端构建依赖)
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "[ERROR] node / npm 不可用, 请先安装 Node.js >=20 (推荐 nvm install 20)" >&2
  exit 1
fi

# 检查前端依赖 (frontend/node_modules), 缺失则自动 npm install
if [ ! -d frontend/node_modules ]; then
  echo "[INFO] 前端依赖缺失, 自动执行 npm install (frontend/)..."
  (cd frontend && npm install)
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
echo "  Air Agent (langgraph-api + assistant-ui Next.js 前端)"
echo "  后端 langgraph-api:  http://${LANGGRAPH_HOST}:${LANGGRAPH_PORT}"
echo "  前端 Next.js:        http://${NEXT_HOST}:${NEXT_PORT}  (打开此地址)"
echo "  LangGraph API Docs:  http://localhost:${LANGGRAPH_PORT}/docs"
echo "  6 个图: basic-qa / intelligent-analysis / data-analysis /"
echo "         intelligent-report / deep-research / intelligent-tracing"
echo "=========================================="

# 启动 Next.js 前端 dev 服务器 (后台)
# 通过 npm --prefix frontend 指定项目目录; -- --port 参数透传给 next dev
nohup npm --prefix frontend run dev -- --port "$NEXT_PORT" --hostname "$NEXT_HOST" \
  > "$NEXT_LOG_FILE" 2>&1 &
NEXT_PID=$!
echo "$NEXT_PID" > "$NEXT_PID_FILE"
disown "$NEXT_PID" 2>/dev/null || true
echo "  前端 Next.js   PID=$NEXT_PID  (log: $NEXT_LOG_FILE)"

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

# Readiness probe 1: 等待 langgraph-api 加载完 6 个图
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

# Readiness probe 2: 等待 Next.js 前端可访问
echo "  等待 Next.js 前端就绪..."
for i in {1..60}; do
  if curl -sf "http://localhost:${NEXT_PORT}/" >/dev/null 2>&1; then
    echo "  ✓ 前端就绪"
    break
  fi
  sleep 1
done

echo
echo "  ✓✓ 全部就绪 ✓✓"
echo "  浏览器打开前端:  http://localhost:${NEXT_PORT}"
echo "  后端 Swagger:   http://localhost:${LANGGRAPH_PORT}/docs#tag/assistants"
echo
exit 0
