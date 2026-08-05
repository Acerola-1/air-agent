"""对比 OpenCode Go 网关上 minimax-m3 与 deepseek-v4-flash 的流式 token 速度.

- minimax-m3: Anthropic 风格 /v1/messages 通道（项目当前在用）
- deepseek-v4-flash: OpenAI 兼容 /v1/chat/completions 通道

指标: TTFT(首 token 延迟)、输出 token 数、生成速率 tokens/s（按流内 usage 统计）。
"""

from __future__ import annotations

import json
import os
import statistics
import time

import httpx
from dotenv import load_dotenv

load_dotenv("/home/ai/app_final_test/.env")

API_KEY = os.environ["OPENCODE_GO_DEEPSEEK_V4_FLASH_API_KEY"]
BASE = "https://opencode.ai/zen/go"
PROMPT = "请用中文写一段约500字的说明，介绍PM2.5和臭氧对人体健康的影响。直接输出正文。"
ROUNDS = 3
MAX_TOKENS = 1024


def bench_anthropic(model: str) -> dict:
    """走 /v1/messages 流式通道."""
    url = f"{BASE}/v1/messages"
    headers = {
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "stream": True,
        "messages": [{"role": "user", "content": PROMPT}],
    }
    t0 = time.perf_counter()
    ttft = None
    out_tokens = 0
    chars = 0
    with httpx.Client(timeout=120) as client:
        with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                evt = json.loads(data)
                etype = evt.get("type")
                if etype == "content_block_delta":
                    text = evt.get("delta", {}).get("text", "")
                    if text and ttft is None:
                        ttft = time.perf_counter() - t0
                    chars += len(text)
                elif etype == "message_delta":
                    usage = evt.get("usage") or {}
                    out_tokens = usage.get("output_tokens", out_tokens)
    total = time.perf_counter() - t0
    return {"ttft": ttft, "total": total, "tokens": out_tokens, "chars": chars}


def bench_openai(model: str) -> dict:
    """走 /v1/chat/completions 流式通道."""
    url = f"{BASE}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": PROMPT}],
    }
    t0 = time.perf_counter()
    ttft = None
    out_tokens = 0
    chars = 0
    with httpx.Client(timeout=120) as client:
        with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                evt = json.loads(data)
                usage = evt.get("usage")
                if usage:
                    out_tokens = usage.get("completion_tokens", out_tokens)
                for choice in evt.get("choices", []):
                    text = (choice.get("delta") or {}).get("content") or ""
                    if text and ttft is None:
                        ttft = time.perf_counter() - t0
                    chars += len(text)
    total = time.perf_counter() - t0
    return {"ttft": ttft, "total": total, "tokens": out_tokens, "chars": chars}


def run(name: str, fn, model: str) -> None:
    """对单一通道跑 ROUNDS 轮并打印均值."""
    print(f"\n===== {name} ({model}) =====")  # noqa: T201
    results = []
    for i in range(ROUNDS):
        try:
            r = fn(model)
        except Exception as e:  # noqa: BLE001
            print(f"  第{i + 1}轮 失败: {type(e).__name__}: {e}")  # noqa: T201
            continue
        gen_time = r["total"] - (r["ttft"] or 0)
        tps = r["tokens"] / gen_time if r["tokens"] and gen_time > 0 else None
        cps = r["chars"] / gen_time if gen_time > 0 else 0
        results.append((r["ttft"], tps, cps, r["tokens"], r["total"]))
        tps_s = f"{tps:.1f}" if tps else "N/A"
        print(  # noqa: T201
            f"  第{i + 1}轮: TTFT={r['ttft']:.2f}s  输出={r['tokens']}tok/"
            f"{r['chars']}字  速率={tps_s} tok/s ({cps:.0f} 字/s)  总耗时={r['total']:.2f}s"
        )
    if results:
        avg_ttft = statistics.mean(x[0] for x in results if x[0])
        tps_list = [x[1] for x in results if x[1]]
        avg_tps = statistics.mean(tps_list) if tps_list else None
        avg_cps = statistics.mean(x[2] for x in results)
        tps_s = f"{avg_tps:.1f} tok/s" if avg_tps else "N/A"
        print(f"  平均: TTFT={avg_ttft:.2f}s  速率={tps_s} ({avg_cps:.0f} 字/s)")  # noqa: T201


if __name__ == "__main__":
    # 交替执行，减小网络波动带来的偏差
    run("MiniMax M3 / Anthropic 通道", bench_anthropic, "minimax-m3")
    run("DeepSeek V4 Flash / OpenAI 通道", bench_openai, "deepseek-v4-flash")
