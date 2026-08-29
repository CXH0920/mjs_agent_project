# -*- coding: utf-8 -*-
"""导入实战配队：外部工具导出 JSON → data/combos.json

- 武将名 → 角色 ID 映射（heroes.json），未匹配项进报告，不静默丢弃；
- note 座次解析（src.data.combo_seats，含别名表与数字前置写法），
  解析失败/部分成功的条目照常导入（座次留空）并列入报告供人工复核；
- 解析结果与 position 字段交叉校验（以 note 为准），不一致清单进报告；
- 幂等：每次按源数据全量重建，重复执行输出稳定。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SOURCE = PROJECT_ROOT / ".tmp_test" / "data.json"
DEFAULT_HEROES = PROJECT_ROOT / "data" / "heroes.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "combos.json"

sys.path.insert(0, str(PROJECT_ROOT))

from src.data.combo_manager import ComboManager  # noqa: E402
from src.data.combo_seats import (  # noqa: E402
    STATUS_NONE,
    STATUS_PARSED,
    parse_seats,
)
from src.data.models import Combo  # noqa: E402


def _load_hero_name_map(heroes_path: Path) -> dict[str, int]:
    heroes_raw = json.loads(Path(heroes_path).read_text(encoding="utf-8"))
    hero_list = heroes_raw["heroes"] if isinstance(heroes_raw, dict) and "heroes" in heroes_raw else heroes_raw
    return {
        hero["name"]: hero["id"]
        for hero in hero_list
        if isinstance(hero, dict) and "name" in hero and "id" in hero
    }


def _check_position_mismatch(combo: Combo, seats1: list[int], seats2: list[int]) -> bool:
    """parsed 座次与 position 摘要是否矛盾（seat 全座 vs 单一 14/23）；both 不校验。"""
    if combo.position == "both":
        return False
    union = sorted(set(seats1) | set(seats2))
    if len(union) == 4:
        return True
    return len(union) == 2 and "".join(str(s) for s in union) != combo.position


def run_import(source_path: Path, heroes_path: Path, output_path: Path) -> dict:
    """执行导入，返回报告 dict。每次全量重建输出文件，重复执行结果稳定。"""
    source = json.loads(Path(source_path).read_text(encoding="utf-8"))
    combos_raw = source.get("combos", []) if isinstance(source, dict) else source
    name2id = _load_hero_name_map(heroes_path)

    report: dict = {
        "total": len(combos_raw),
        "imported": 0,
        "unmatched": [],
        "duplicates": [],
        "invalid": [],
        "seat_stats": {"parsed": 0, "none": 0, "partial": 0, "unparsed": 0},
        "seat_review": [],
        "position_mismatch": [],
    }

    manager = ComboManager(output_path)
    manager.clear_all()

    seen_keys: set[tuple[int, int]] = set()
    for index, raw in enumerate(combos_raw):
        name1, name2 = str(raw.get("hero1", "")).strip(), str(raw.get("hero2", "")).strip()
        id1, id2 = name2id.get(name1), name2id.get(name2)
        if not id1 or not id2:
            report["unmatched"].append({"index": index, "hero1": name1, "hero2": name2})
            continue
        key = tuple(sorted((id1, id2)))
        if key in seen_keys:
            report["duplicates"].append({"index": index, "hero1": name1, "hero2": name2})
            continue

        note = str(raw.get("note", ""))
        status, seats1, seats2 = parse_seats(note, name1, name2)
        report["seat_stats"][status] += 1
        if status not in (STATUS_PARSED, STATUS_NONE):
            report["seat_review"].append({"index": index, "hero1": name1, "hero2": name2, "note": note})

        try:
            combo = Combo(
                hero1_name=name1,
                hero2_name=name2,
                hero1_id=id1,
                hero2_id=id2,
                rating=int(raw.get("rating", 0)),
                position=str(raw.get("position", "both")),
                note=note,
                hero1_seats=seats1,
                hero2_seats=seats2,
            )
        except Exception as error:
            report["invalid"].append({"index": index, "hero1": name1, "hero2": name2, "error": str(error)})
            continue

        if status == STATUS_PARSED and _check_position_mismatch(combo, seats1, seats2):
            report["position_mismatch"].append(
                {"index": index, "hero1": name1, "hero2": name2, "position": combo.position, "note": note}
            )

        seen_keys.add(key)
        manager.update(combo, key)
        report["imported"] += 1

    manager.save()
    return report


def _print_report(report: dict) -> None:
    seats = report["seat_stats"]
    print(f"导入完成：源 {report['total']} 条 → 写入 {report['imported']} 条")
    print(f"座次解析：成功 {seats['parsed']} + 无要求 {seats['none']}"
          f" + 部分 {seats['partial']} + 失败 {seats['unparsed']}")
    if report["unmatched"]:
        print(f"⚠ 未匹配武将 {len(report['unmatched'])} 条：")
        for item in report["unmatched"]:
            print(f"  #{item['index']} {item['hero1']} + {item['hero2']}")
    if report["duplicates"]:
        print(f"⚠ 重复配对 {len(report['duplicates'])} 条（保留首条）：")
        for item in report["duplicates"]:
            print(f"  #{item['index']} {item['hero1']} + {item['hero2']}")
    if report["invalid"]:
        print(f"⚠ 字段校验失败 {len(report['invalid'])} 条：")
        for item in report["invalid"]:
            print(f"  #{item['index']} {item['hero1']} + {item['hero2']}: {item['error']}")
    if report["seat_review"]:
        print(f"⚠ 座次需人工复核 {len(report['seat_review'])} 条（已按无座次导入）：")
        for item in report["seat_review"]:
            print(f"  #{item['index']} {item['hero1']} + {item['hero2']} | {item['note'][:40]}")
    if report["position_mismatch"]:
        print(f"⚠ 座次与 position 字段不一致 {len(report['position_mismatch'])} 条（以 note 为准，position 留原值）：")
        for item in report["position_mismatch"]:
            print(f"  #{item['index']} {item['hero1']} + {item['hero2']} | position={item['position']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="导入实战配队数据")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="外部工具导出 JSON 路径")
    parser.add_argument("--heroes", type=Path, default=DEFAULT_HEROES, help="heroes.json 路径")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="输出 combos.json 路径")
    args = parser.parse_args()
    report = run_import(args.source, args.heroes, args.output)
    _print_report(report)


if __name__ == "__main__":
    main()
