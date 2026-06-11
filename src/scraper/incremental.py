"""
名将杀 Agent - 增量爬虫

功能：
  1. 增量采集（--incremental）：只爬取本地 data/heroes.json 中还没有的武将，追加写入
  2. 指定武将（--hero / --hero-id）：按名称或 ID 爬取指定武将（先删除旧数据再写入新数据）

清洗逻辑复用 src.scraper.official：
  _fetch → _transform → _validate_heroes → 同上
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# 复用爬虫核心模块的公开 API
from src.scraper.crawler import (
    fetch,
    find_chunk_url,
    extract_js_array,
    js_to_json,
    transform,
    validate_heroes,
    fetch_all_raw,
)

logger = logging.getLogger(__name__)

# 默认数据路径
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_HEROES_FILE = DEFAULT_DATA_DIR / "heroes.json"


def load_existing_ids(path: Path) -> set[int]:
    """加载本地已有武将的 ID 集合"""
    if not path.exists():
        return set()
    with open(path, "r", encoding="utf-8") as f:
        heroes = json.load(f)
    existing = set()
    for h in heroes:
        hid = h.get("id")
        if hid is not None:
            existing.add(int(hid))
    logger.info("本地已有 %d 个武将 (ID: %s .. %s)", len(existing),
                 min(existing) if existing else "?", max(existing) if existing else "?")
    return existing


def load_existing_names(path: Path) -> dict[str, int]:
    """加载本地已有武将的 {名称: ID} 映射"""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        heroes = json.load(f)
    return {h["name"]: h["id"] for h in heroes if h.get("name")}


def filter_by_ids(raw_list: list[dict], target_ids: set[int]) -> list[dict]:
    """按 ID 筛选"""
    return [r for r in raw_list if r.get("id") in target_ids]


def filter_by_names(raw_list: list[dict], target_names: list[str]) -> list[dict]:
    """按名称筛选（支持模糊匹配，只要有包含关系就算）"""
    results = []
    matched = set()
    for name in target_names:
        for r in raw_list:
            rn = r.get("name", "")
            if name in rn or rn in name:
                if r.get("id") not in matched:
                    results.append(r)
                    matched.add(r.get("id"))
    return results


def incremental_collect(
    raw_list: list[dict],
    existing_ids: set[int],
) -> list[dict]:
    """筛除本地已存在的武将"""
    new_raw = [r for r in raw_list if r.get("id") not in existing_ids]
    logger.info("\u589e\u91cf\u7b5b\u9664: \u5b98\u7f51 %d \u6761 - \u672c\u5730 %d \u6761 = \u65b0 %d \u6761",
                len(raw_list), len(existing_ids), len(new_raw))
    return new_raw


def run(raw_list: list[dict], output_path: Path, dry_run: bool,
        append: bool = False, replace_ids: set[int] | None = None) -> None:
    """对原始数据执行清洗→校验→输出

    append=True : 追加模式，只添加本地不存在的武将（增量采集用）
    replace_ids : 替换模式，先删除这些 ID 的旧数据，再加入新数据（指定武将采集用）
    两者皆否    : 全量覆盖写入
    """
    print("\n[清洗与字段映射...]", flush=True)
    transformed = [transform(r) for r in raw_list if transform(r)]
    print(f"  -> \u6e05\u6d17\u540e: {len(transformed)} \u6761", flush=True)

    if not transformed:
        print("  \u65e0\u65b0\u6570\u636e\u9700\u8981\u5904\u7406\u3002", flush=True)
        return

    print("\n[Pydantic \u6a21\u578b\u6821\u9a8c...]", flush=True)
    validated = validate_heroes(transformed)
    print(f"  -> \u6821\u9a8c\u901a\u8fc7: {len(validated)} \u6761", flush=True)

    # 预览模式
    if dry_run:
        print("\n  [\u9884\u89c8]", flush=True)
        for h in validated[:5]:
            sk = ", ".join(s["name"] for s in h["skills"])
            print(f"    ID={h['id']:>3}  {h['name']}  [{h['faction']}]  {sk}", flush=True)
        if len(validated) > 5:
            print(f"    ... \u5171 {len(validated)} \u6761", flush=True)
        return

    # 写入模式
    if not output_path.exists():
        merged = validated
    elif replace_ids is not None:
        with open(output_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        before = len(existing)
        existing = [h for h in existing if h["id"] not in replace_ids]
        removed = before - len(existing)
        merged = existing + validated
        print(f"  -> \u66ff\u6362\u5199\u5165: \u5220\u9664 {removed} \u6761\u65e7\u6570\u636e + \u5199\u5165 {len(validated)} \u6761\u65b0\u6570\u636e", flush=True)
    elif append:
        with open(output_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        existing_ids = {h["id"] for h in existing}
        merged = existing + [h for h in validated if h["id"] not in existing_ids]
        print(f"  -> \u8ffd\u52a0\u5199\u5165: \u539f\u6709 {len(existing)} + \u65b0\u589e {len(validated) - (len(merged) - len(existing))}", flush=True)
    else:
        merged = validated

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"  -> \u5df2\u4fdd\u5b58: {output_path} ({len(merged)} \u6761)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="\u540d\u5c06\u6740\u589e\u91cf\u722c\u866b")
    parser.add_argument("--incremental", action="store_true",
                        help="\u589e\u91cf\u6a21\u5f0f\uff1a\u53ea\u722c\u53d6\u672c\u5730\u8fd8\u672a\u62e5\u6709\u7684\u6b66\u5c06\uff0c\u8ffd\u52a0\u5199\u5165")
    parser.add_argument("--hero", "-n", type=str,
                        help="\u6309\u6b66\u5c06\u540d\u79f0\u91c7\u96c6\uff08\u591a\u4e2a\u7528\u9017\u53f7\u5206\u9694\uff0c\u652f\u6301\u6a21\u7cca\u5339\u914d\uff09")
    parser.add_argument("--hero-id", type=str,
                        help="\u6309\u6b66\u5c06 ID \u91c7\u96c6\uff08\u591a\u4e2a\u7528\u9017\u53f7\u5206\u9694\uff0c\u5982 114,115\uff09")
    parser.add_argument("--output", "-o", type=str,
                        help="\u8f93\u51fa\u6587\u4ef6\u8def\u5f84\uff08\u9ed8\u8ba4 data/heroes.json\uff09")
    parser.add_argument("--dry-run", action="store_true",
                        help="\u9884\u89c8\u6a21\u5f0f\uff0c\u4e0d\u5199\u5165\u6587\u4ef6")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="\u8be6\u7ec6\u65e5\u5fd7")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    if not any([args.incremental, args.hero, args.hero_id]):
        parser.error("\u8bf7\u6307\u5b9a --incremental \u548c/\u6216 --hero / --hero-id")

    output_path = Path(args.output) if args.output else DEFAULT_HEROES_FILE

    print("=" * 60, flush=True)
    print("  \u540d\u5c06\u6740 Agent - \u589e\u91cf\u722c\u866b", flush=True)
    print("=" * 60, flush=True)
    mode = "\u9884\u89c8" if args.dry_run else str(output_path)
    print(f"  \u8f93\u51fa: {mode}", flush=True)

    # 1. 获取官网全量数据
    print("\n[1/3] \u83b7\u53d6\u5b98\u7f51\u6570\u636e...", flush=True)
    all_raw = fetch_all_raw()

    # 2. 筛选目标
    print("\n[2/3] \u7b5b\u9009\u76ee\u6807\u6b66\u5c06...", flush=True)
    target_raw = list(all_raw)

    if args.incremental:
        existing_ids = load_existing_ids(output_path)
        target_raw = incremental_collect(all_raw, existing_ids)
        print(f"  \u589e\u91cf\u76ee\u6807: {len(target_raw)} \u4e2a\u6b66\u5c06\u8981\u5904\u7406", flush=True)

    if args.hero:
        names = [n.strip() for n in args.hero.split(",") if n.strip()]
        filtered = filter_by_names(all_raw, names)
        print(f"  \u6309\u540d\u79f0\u7b5b\u9009: {names} -> \u5339\u914d {len(filtered)} \u6761", flush=True)
        if args.incremental:
            target_ids = {r.get("id") for r in target_raw}
            filtered = [r for r in filtered if r.get("id") in target_ids]
        target_raw = filtered

    if args.hero_id:
        ids = set()
        for hid in args.hero_id.split(","):
            hid = hid.strip()
            try:
                ids.add(int(hid))
            except ValueError:
                logger.warning("\u65e0\u6548 ID: %s", hid)
        filtered = filter_by_ids(all_raw, ids)
        print(f"  \u6309 ID \u7b5b\u9009: {sorted(ids)} -> \u5339\u914d {len(filtered)} \u6761", flush=True)
        if args.incremental:
            target_ids = {r.get("id") for r in target_raw}
            filtered = [r for r in filtered if r.get("id") in target_ids]
        target_raw = filtered

    if not target_raw:
        print("\n  \u65e0\u76ee\u6807\u6b66\u5c06\uff0c\u9000\u51fa\u3002", flush=True)
        return

    # 3. 清洗 + 校验 + 输出
    print("\n[3/3] \u6e05\u6d17\u4e0e\u5199\u5165...", flush=True)

    # 指定武将模式（不含 --incremental）：先删旧数据再写入新数据
    replace_ids = None
    if (args.hero or args.hero_id) and not args.incremental:
        replace_ids = {r["id"] for r in target_raw if r.get("id") is not None}

    run(target_raw, output_path, dry_run=args.dry_run,
        append=args.incremental, replace_ids=replace_ids)

    print(f"\n{'=' * 60}", flush=True)
    print(f"  \u5b8c\u6210!", flush=True)
    print(f"{'=' * 60}", flush=True)


if __name__ == "__main__":
    main()
