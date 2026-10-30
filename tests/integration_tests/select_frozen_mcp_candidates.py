"""Real configured model selection from the frozen retrieval Top 3 only.

No user conversation, credentials, response data or full schema is sent.
This selector benchmark is separate from graph execution acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from openai import AsyncOpenAI


async def main(args):
    if args.out.exists():
        raise FileExistsError(args.out)
    corpus = json.loads(args.schemas.read_text())
    documents = {"mcp:ipp:" + row["name"]: row for row in corpus}
    frozen = json.loads(args.retrieval.read_text())
    client = AsyncOpenAI(
        api_key=os.environ["MAIN_LLM_API_KEY"],
        base_url=os.environ["MAIN_LLM_BASE_URL"],
        max_retries=0,
        timeout=60,
    )
    report = {
        "evidence_kind": "configured_real_model_selection_from_frozen_actual_top3",
        "model": os.environ["MAIN_LLM_MODEL"],
        "model_settings": {
            "max_completion_tokens": args.max_output_tokens,
            "temperature": 0.1,
        },
        "catalog_version": frozen["catalog_version"],
        "seeds": [],
        "limitations": [
            "does not execute tools or validate graph routing",
            "original numeric ranking scores not retained; actual ordered IDs and brief purposes used",
            "fixed 12 questions do not establish general recall or selection accuracy",
        ],
    }
    try:
        for seed in frozen["seeds"]:
            candidates = []
            for key in seed["candidates"]:
                row = documents[key]
                purpose = " ".join(
                    line
                    for line in row["description"]
                    .split("【返回字段", 1)[0]
                    .splitlines()
                    if not line.lstrip().startswith("|")
                )[:500]
                candidates.append(
                    {"tool_id": key, "name": row["name"], "description": purpose}
                )
            start = time.monotonic()
            response = await client.chat.completions.create(
                model=os.environ["MAIN_LLM_MODEL"],
                messages=[
                    {
                        "role": "system",
                        "content": '为给定环境监测查询选择可以直接解决问题的候选工具，可选零个或多个。只返回JSON {"selected_tool_ids": [...]}，不得选择候选外ID，不执行工具。',
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"query": seed["query"], "candidates": candidates},
                            ensure_ascii=False,
                        ),
                    },
                ],
                temperature=0.1,
                max_completion_tokens=args.max_output_tokens,
            )
            text = response.choices[0].message.content or ""
            try:
                payload = json.loads(
                    text.strip().removeprefix("```json").removesuffix("```").strip()
                )
                selected = payload["selected_tool_ids"]
                valid = (
                    isinstance(selected, list)
                    and response.choices[0].finish_reason == "stop"
                    and all(isinstance(x, str) for x in selected)
                    and set(selected) <= set(seed["candidates"])
                )
            except (ValueError, TypeError, KeyError):
                selected, valid = [], False
            report["seeds"].append(
                {
                    "query": seed["query"],
                    "candidates": seed["candidates"],
                    "gold": seed["gold"],
                    "selected": selected,
                    "valid_candidate_selection": valid,
                    "finish_reason": response.choices[0].finish_reason,
                    "raw_final_content": text,
                    "selected_gold": valid and bool(set(selected) & set(seed["gold"])),
                    "elapsed_ms": (time.monotonic() - start) * 1000,
                    "usage": response.usage.model_dump() if response.usage else None,
                }
            )
            args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        report["selected_gold_count"] = sum(s["selected_gold"] for s in report["seeds"])
        report["total"] = len(report["seeds"])
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--schemas", type=Path, required=True)
    parser.add_argument("--retrieval", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    asyncio.run(main(parser.parse_args()))
