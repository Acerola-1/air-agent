"""把通过的校准报告写入分析 profile（部署/重建前的显式步骤）.

安全边界：
- 只接受 `real_embedding` 且 `passed=true` 的报告；fixture 报告拒绝；
- 默认只允许 draft/ready profile（激活前配置）；对 active profile 必须显式
  `--allow-active`，且仍要求同一 embedding profile/model/dimension，避免静默
  改变历史簇的向量空间；
- 更新 thresholds/calibration/revision 并保留原 profile 身份。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.config import get_settings  # noqa: E402


def _load_report(path: Path) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("provider", {}).get("provider") != "real_embedding":
        raise SystemExit("只接受 real_embedding 校准报告；fixture 不能写 profile")
    if not report.get("passed"):
        raise SystemExit("校准报告未通过门槛；不能写入 profile")
    thresholds = report.get("thresholds") or {}
    for key in ("similarity", "margin", "review_low"):
        value = thresholds.get(key)
        if not isinstance(value, (int, float)) or not 0 < float(value) <= 1:
            raise SystemExit(f"报告阈值 {key} 非法")
    return report


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, type=uuid.UUID)
    parser.add_argument("--profile-key", default="v1")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--allow-active", action="store_true")
    args = parser.parse_args()
    report = _load_report(args.report)
    provider = report["provider"]
    thresholds = report["thresholds"]
    cal = {
        "passed": True,
        "precision": report["holdout"]["precision"],
        "coverage": report["holdout"]["coverage"],
        "auto_classified": report["holdout"]["auto_classified"],
        "false_merges": report["holdout"]["false_merges"],
        "sensitive_leaks": report["holdout"]["sensitive_leaks"],
        "sample_counts": report["samples"],
        "report_provider": provider["provider"],
        "model": provider.get("model"),
        "profile": provider.get("profile"),
        "thresholds": thresholds,
    }
    engine = create_async_engine(get_settings().sqlalchemy_uri)
    try:
        async with engine.begin() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT id, state, embedding_profile, embedding_model,"
                        " embedding_dimension, revision FROM agent_insight_profiles"
                        " WHERE tenant_id=:t AND profile_key=:k"
                        " ORDER BY profile_version DESC LIMIT 1"
                    ),
                    {"t": args.tenant_id, "k": args.profile_key},
                )
            ).first()
            if row is None:
                raise SystemExit("目标 profile 不存在")
            if row.state == "active" and not args.allow_active:
                raise SystemExit("active profile 需 --allow-active；推荐新建版本后切换")
            if (
                row.embedding_profile != provider.get("profile")
                or row.embedding_model != provider.get("model")
                or int(row.embedding_dimension) != int(provider.get("dimension"))
            ):
                raise SystemExit("报告 embedding profile/model/dimension 与目标不一致")
            await connection.execute(
                text(
                    "UPDATE agent_insight_profiles SET thresholds=CAST(:thresholds AS jsonb),"
                    " calibration=CAST(:calibration AS jsonb), revision=revision+1,"
                    " updated_at=now() WHERE id=:id AND tenant_id=:t"
                ),
                {
                    "thresholds": json.dumps(thresholds, ensure_ascii=False),
                    "calibration": json.dumps(cal, ensure_ascii=False),
                    "id": row.id,
                    "t": args.tenant_id,
                },
            )
        print(f"已写入 profile {row.id}（state={row.state}，revision->{row.revision + 1}）")
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
