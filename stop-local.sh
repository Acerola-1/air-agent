#!/bin/bash
# ============================================
# 停止 run-local.sh 启动的 langgraph-api + 静态前端进程
# ============================================
set -e

LANGGRAPH_PID_FILE="/tmp/air-agent-langgraph.pid"
STATIC_PID_FILE="/tmp/air-agent-static.pid"

stop_one() {
  local name="$1"
  local pid_file="$2"
  if [ -f "$pid_file" ]; then
    local pid
    pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      echo "  停止 $name (PID=$pid)"
    fi
    rm -f "$pid_file"
  fi
}

stop_one "langgraph-api" "$LANGGRAPH_PID_FILE"
stop_one "静态前端"      "$STATIC_PID_FILE"

exit 0
