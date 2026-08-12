#!/bin/bash
# ============================================
# 启动 Air Agent（容器化开发模式）
# 架构:
#   - 后端: container-compose 拉起 postgres + redis + langgraph-api(官方镜像)
#           —— 存储统一落 PG 容器(命名卷 pgdata), 本地代码 src/ 挂载进容器,
#             改代码后跑 `container-compose up -d --profile prod` 即生效(无需重建镜像)
#   - 前端: Next.js (原生 LangGraph SDK) :3000
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

# 检查容器系统已启动
if ! container system status >/dev/null 2>&1; then
  echo "[ERROR] Apple Container 系统未启动, 请先执行: container system start" >&2
  exit 1
fi

# 检查 container-compose
if ! command -v container-compose >/dev/null 2>&1; then
  echo "[ERROR] container-compose 不可用 (https://github.com/Mcrich23/Container-Compose)" >&2
  exit 1
fi

# 检查前端依赖 (frontend/node_modules), 缺失则自动 npm install
if [ ! -d frontend/node_modules ]; then
  echo "[INFO] 前端依赖缺失, 自动执行 npm install (frontend/)..."
  (cd frontend && npm install)
fi

# ── 后端: 容器化 langgraph-api (postgres + redis + langgraph-api) ──
# 本地代码已通过 docker-compose.yaml 挂载 ./src:/deps/air_agent/src,
# 修改 src/ 后重跑本脚本(或 container-compose up -d --profile prod)即生效。
echo "=========================================="
echo "  Air Agent (容器化开发模式)"
echo "  后端 langgraph-api(容器): http://${LANGGRAPH_HOST}:${LANGGRAPH_PORT}"
echo "  前端 Next.js:             http://${NEXT_HOST}:${NEXT_PORT}  (打开此地址)"
echo "  LangGraph API Docs:       http://localhost:${LANGGRAPH_PORT}/docs"
echo "  存储: PostgreSQL 容器 (命名卷 pgdata), 本地 SQLite 已弃用"
echo "  6 个图: basic-qa / intelligent-analysis / data-analysis /"
echo "          intelligent-report / deep-research / intelligent-tracing"
echo "=========================================="

container-compose up -d --profile prod

# ── 前端: Next.js dev 服务器 (后台) ──
# 通过 npm --prefix frontend 指定项目目录; -- --port 参数透传给 next dev
nohup npm --prefix frontend run dev -- --port "$NEXT_PORT" --hostname "$NEXT_HOST" \
  > "$NEXT_LOG_FILE" 2>&1 &
NEXT_PID=$!
echo "$NEXT_PID" > "$NEXT_PID_FILE"
disown "$NEXT_PID" 2>/dev/null || true
echo "  前端 Next.js   PID=$NEXT_PID  (log: $NEXT_LOG_FILE)"

# Readiness probe 1: 等待 langgraph-api 容器加载完 6 个图
echo "  等待 langgraph-api 就绪..."
for i in {1..90}; do
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
echo "  改后端代码后更新:  container-compose up -d --profile prod   (或重跑本脚本)"
echo "  停容器:            container-compose down"
echo
exit 0
