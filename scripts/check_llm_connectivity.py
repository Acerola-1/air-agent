"""独立检查主LLM配置，只发送一次请求，不启动Agent.

这是直接向终端输出结果的独立CLI脚本。
"""

# ruff: noqa: T201

from pathlib import Path
from time import monotonic

import httpx
from dotenv import dotenv_values


def main() -> int:
    """发送最小请求并返回进程退出码."""
    config = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    base_url = (config.get("MAIN_LLM_BASE_URL") or "").rstrip("/")
    model = config.get("MAIN_LLM_MODEL") or ""
    key = config.get("MAIN_LLM_API_KEY") or ""
    if not all((base_url, model, key)):
        print("失败：.env 缺少主模型地址、名称或密钥。")
        return 1

    print(f"测试模型：{model}（单次请求，最长等待60秒）", flush=True)
    started = monotonic()
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "请只回复 OK。"}],
                "max_completion_tokens": 1024,
                "stream": False,
            },
            timeout=httpx.Timeout(60, connect=5),
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"].get("content")
        if not isinstance(content, str) or not content.strip():
            print("失败：接口返回成功状态，但没有回答正文。")
            return 1
        print(f"成功：{content.strip()[:100]}；耗时 {monotonic() - started:.2f} 秒")
        print(f"响应模型：{body.get('model', '未返回')}")
        return 0
    except httpx.HTTPStatusError as exc:
        print(f"失败：HTTP {exc.response.status_code}（未输出凭据或响应体）。")
    except (httpx.RequestError, ValueError, KeyError, IndexError, TypeError) as exc:
        print(f"失败：{type(exc).__name__}；耗时 {monotonic() - started:.2f} 秒")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
