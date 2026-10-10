"""导入全国省/市/区县三级行政区划到 `regions` 表。幂等，可反复执行。

    python scripts/import_regions.py

数据源：`datasets/pca-code.json`
（modood/Administrative-divisions-of-China，见 datasets/README.md 的来源与版本记录）

导入规则：
1. 省(2 位)/市(4 位) 的 code 左侧补齐到 6 位，对齐 GB/T 2260 标准编码；
   区县本身就是 6 位，原样保留。
2. **只收 6 位 code 的区县**。数据集中有 82 个 9 位 code，实际是乡镇/街道级，
   全部属于东莞市、中山市、儋州市、嘉峪关市这 4 个「直筒子市」（行政上不设区县，
   直接管辖乡镇）。按「最小只能选到区县」的要求排除——这 4 个市到市级即可选。
3. 港澳台数据在 `datasets/HK-MO-TW.json`，结构与大陆不同（顶层是 dict），暂不导入。
4. 只做 upsert，不删除数据库里已有的行——避免误删被用户关注引用的区划。
   数据集若发生区划撤并，需要人工确认后再清理。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Region  # noqa: E402

DATASET = Path(__file__).resolve().parent.parent / "datasets" / "pca-code.json"
STANDARD_CODE_LENGTH = 6


def _pad(code: str) -> str:
    """把 2 位省码 / 4 位市码右侧补零到 6 位（如 `11`→`110000`、`1101`→`110100`）。"""
    return code.ljust(STANDARD_CODE_LENGTH, "0")


def load_dataset() -> tuple[list[tuple[str, str, int, str | None, int]], list[str]]:
    """返回 (待导入行, 被跳过的乡镇级节点描述)。"""
    raw = json.loads(DATASET.read_text(encoding="utf-8"))
    rows: list[tuple[str, str, int, str | None, int]] = []
    skipped: list[str] = []

    for p_index, province in enumerate(raw):
        province_code = _pad(province["code"])
        rows.append((province_code, province["name"], 1, None, p_index))

        for c_index, city in enumerate(province.get("children", [])):
            city_code = _pad(city["code"])
            rows.append((city_code, city["name"], 2, province_code, c_index))

            for a_index, area in enumerate(city.get("children", [])):
                if len(area["code"]) != STANDARD_CODE_LENGTH:
                    skipped.append(
                        f"{province['name']}/{city['name']}/{area['name']} ({area['code']})"
                    )
                    continue
                rows.append((area["code"], area["name"], 3, city_code, a_index))

    return rows, skipped


async def import_regions() -> None:
    rows, skipped = load_dataset()
    inserted = updated = 0

    async with SessionLocal() as session:
        for code, name, level, parent_code, sort in rows:
            existing = await session.get(Region, code)
            if existing is None:
                session.add(
                    Region(code=code, name=name, level=level, parent_code=parent_code, sort=sort)
                )
                inserted += 1
            else:
                existing.name = name
                existing.level = level
                existing.parent_code = parent_code
                existing.sort = sort
                updated += 1
        await session.commit()

        total = await session.scalar(select(func.count()).select_from(Region))
        by_level = {
            level: await session.scalar(
                select(func.count()).select_from(Region).where(Region.level == level)
            )
            for level in (1, 2, 3)
        }

    print(f"  新增 {inserted} 行，更新 {updated} 行")
    print(f"  跳过乡镇/街道级节点 {len(skipped)} 个（直筒子市，到市级为止）")
    if skipped:
        cities = sorted({"/".join(s.split("/")[:2]) for s in skipped})
        print(f"  涉及城市: {', '.join(cities)}")
    print(f"  regions 现有 {total} 行 —— 省 {by_level[1]} / 市 {by_level[2]} / 区县 {by_level[3]}")


if __name__ == "__main__":
    if not DATASET.exists():
        raise SystemExit(f"数据集不存在: {DATASET}")
    asyncio.run(import_regions())
