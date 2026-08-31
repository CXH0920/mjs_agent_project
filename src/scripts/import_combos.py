# -*- coding: utf-8 -*-
"""导入实战配队 CLI：外部工具导出 JSON → data/combos.json

导入逻辑在 src/business/maintenance/combo_import_service.py（与 UI 导入对话框
共用）；本模块只保留命令行入口与报表打印。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from src.business.maintenance.combo_import_service import (
    DEFAULT_HEROES,
    DEFAULT_OUTPUT,
    run_import,
)


def _print_report(report: dict) -> None:
    seats = report["seat_stats"]
    print(f"导入完成：源 {report['total']} 条 → 写入 {report['imported']} 条")
    print(f"座次解析：成功 {seats['parsed']} + 无要求 {seats['none']}"
          f" + 部分 {seats['partial']} + 失败 {seats['unparsed']}")
    if report["manual_kept"]:
        print(f"手工记录保留 {len(report['manual_kept'])} 条（不在本次导出中）：")
        for item in report["manual_kept"]:
            print(f"  {item['hero1']} + {item['hero2']} | {item['note'][:40]}")
    if report["manual_collisions"]:
        print(f"⚠ 与手工记录冲突 {len(report['manual_collisions'])} 条（已保留手工版本）：")
        for item in report["manual_collisions"]:
            print(f"  源 #{item['index']} {item['hero1']} + {item['hero2']}")
    if report["removed_stale"]:
        print(f"⚠ 源中已不存在而移除 {len(report['removed_stale'])} 条：")
        for item in report["removed_stale"]:
            print(f"  {item['hero1']} + {item['hero2']}")
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
    parser.add_argument("--source", type=Path, required=True, help="外部工具导出 JSON 路径")
    parser.add_argument("--heroes", type=Path, default=DEFAULT_HEROES, help="heroes.json 路径")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="输出 combos.json 路径")
    args = parser.parse_args()
    report = run_import(args.source, args.heroes, args.output)
    _print_report(report)


if __name__ == "__main__":
    main()
