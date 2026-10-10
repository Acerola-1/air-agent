#!/usr/bin/env bash
# session token 跨语言兼容性校验。
#
# 同一个 token 格式现在有三处消费者（前端 edge middleware 签发/验签、Python 后台
# 签发/验签）。任何一处漂移都会让用户登录态莫名失效，所以这里用固定向量把
# Python 实现 (`app/security.py`) 和 JS 参考实现 (`tests/session_compat.mjs`)
# 逐字节锁死。
#
# 用法: ./tests/check_session_compat.sh
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x .venv/bin/python ]]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
fi

# 固定向量，保证可复现
SECRET='compat-check-secret-do-not-use'
USER_ID='0192f3a4-5b6c-7d8e-9f00-112233445566'
ACCOUNT='root_user-1'
EXP='1893456000'

failures=0
check() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    printf '  ok   %s\n' "$label"
  else
    printf '  FAIL %s\n       expected: %s\n       actual:   %s\n' "$label" "$expected" "$actual"
    failures=$((failures + 1))
  fi
}

py_sign()   { "$PYTHON" tests/session_compat.py sign "$USER_ID" "$ACCOUNT" "$EXP" "$SECRET"; }
py_verify() { "$PYTHON" tests/session_compat.py verify "$1" "$SECRET"; }
js_sign()   { node tests/session_compat.mjs sign "$USER_ID" "$ACCOUNT" "$EXP" "$SECRET"; }
js_verify() { node tests/session_compat.mjs verify "$1" "$SECRET"; }

echo "session token 跨语言兼容性（Python vs JS）"

PY_TOKEN="$(py_sign)"
JS_TOKEN="$(js_sign)"

check "两边签发的 token 完全一致" "$PY_TOKEN" "$JS_TOKEN"
check "Python 能验 JS 签发的 token" \
  "{\"userId\":\"$USER_ID\",\"account\":\"$ACCOUNT\",\"exp\":$EXP}" "$(py_verify "$JS_TOKEN")"
check "JS 能验 Python 签发的 token" \
  "{\"userId\":\"$USER_ID\",\"account\":\"$ACCOUNT\",\"exp\":$EXP}" "$(js_verify "$PY_TOKEN")"
check "错误密钥被 Python 拒绝" "null" \
  "$("$PYTHON" tests/session_compat.py verify "$PY_TOKEN" wrong-secret)"
check "错误密钥被 JS 拒绝" "null" "$(node tests/session_compat.mjs verify "$PY_TOKEN" wrong-secret)"

if [[ "$failures" -gt 0 ]]; then
  echo "结果: $failures 项失败"
  exit 1
fi
echo "结果: 全部通过"
