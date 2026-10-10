"""session token 兼容性校验的 Python 侧 CLI。

与 `session_compat.mjs` 对称，供 `check_session_compat.sh` 调用做逐字节比对。
本文件不 spawn 任何进程——跨语言比对由外层 shell 脚本负责。

用法:
    python tests/session_compat.py sign   <userId> <account> <exp> <secret>
    python tests/session_compat.py verify <token>  <secret>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 允许直接以脚本方式运行（`app` 包在上一层目录）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.security import build_body, sign_body, verify_session  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    command = argv[1]
    if command == "sign" and len(argv) == 6:
        _, _, user_id, account, exp, secret = argv
        sys.stdout.write(sign_body(build_body(user_id, account, int(exp)), secret))
        return 0

    if command == "verify" and len(argv) == 4:
        _, _, token, secret = argv
        payload = verify_session(token, secret)
        sys.stdout.write(
            json.dumps(
                {"userId": payload.user_id, "account": payload.account, "exp": payload.exp},
                separators=(",", ":"),
            )
            if payload
            else "null"
        )
        return 0

    print(f"参数不正确: {argv[1:]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
