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
    logger.info("增量筛选: 官网 %d 条 - 本地 %d 条 = 新 %d 条",
                len(raw_list), len(existing_ids), len(new_raw))
    return new_raw


def run(raw_list: list[dict], output_path: Path, dry_run: bool,
        append: bool = False, replace_ids: set[int] | None = None,
        skip_images: bool = False) -> None:
    """对原始数据执行清洗→校验→输出

    append=True : 追加模式，只添加本地不存在的武将（增量采集用）
    replace_ids : 替换模式，先删除这些 ID 的旧数据，再加入新数据（指定武将采集用）
    两者皆否    : 全量覆盖写入
    """
    print("\n[清洗与字段映射...]", flush=True)
    transformed = [transform(r) for r in raw_list if transform(r)]
    print(f"  -> 清洗后: {len(transformed)} 条", flush=True)

    if not transformed:
        print("  无新数据需要处理。", flush=True)
        return

    print("\n[Pydantic 模型校验...]", flush=True)
    validated = validate_heroes(transformed)
    print(f"  -> 校验通过: {len(validated)} 条", flush=True)

    # 预览模式
    if dry_run:
        print("\n  [预览]", flush=True)
        for h in validated[:5]:
            sk = ", ".join(s["name"] for s in h["skills"])
            print(f"    ID={h['id']:>3}  {h['name']}  [{h['faction']}]  {sk}", flush=True)
        if len(validated) > 5:
            print(f"    ... 共 {len(validated)} 条", flush=True)
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
        print(f"  -> 替换写入: 删除 {removed} 条旧数据 + 写入 {len(validated)} 条新数据", flush=True)
    elif append:
        with open(output_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        existing_ids = {h["id"] for h in existing}
        merged = existing + [h for h in validated if h["id"] not in existing_ids]
        print(f"  -> 追加写入: 原有 {len(existing)} + 新增 {len(validated) - (len(merged) - len(existing))}", flush=True)
    else:
        merged = validated

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # 原子写入：先写 .tmp 再 rename
    tmp_path = output_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    tmp_path.replace(output_path)
    print(f"  -> 已保存: {output_path} ({len(merged)} 条)", flush=True)

    if not dry_run and not skip_images and raw_list:
        from src.scraper.crawler import download_hero_images
        n = download_hero_images(raw_list)
        print(f"  头像已下载: {n} 张", flush=True)


def main() -> None:
    # Windows cmd 默认 GBK，刷新 stdout/stderr 编码以支持中文输出
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="名将杀增量爬虫")
    parser.add_argument("--incremental", action="store_true",
                        help="增量模式：只爬取本地还未拥有的武将，追加写入")
    parser.add_argument("--hero", "-n", type=str,
                        help="按武将名称采集（多个用逗号分隔，支持模糊匹配）")
    parser.add_argument("--hero-id", type=str,
                        help="按武将 ID 采集（多个用逗号分隔，如 114,115）")
    parser.add_argument("--output", "-o", type=str,
                        help="输出文件路径（默认 data/heroes.json）")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览模式，不写入文件")
    parser.add_argument("--skip-images", action="store_true",
                        help="跳过头像下载")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="详细日志")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    if not any([args.incremental, args.hero, args.hero_id]):
        parser.error("请指定 --incremental 和/或 --hero / --hero-id")

    output_path = Path(args.output) if args.output else DEFAULT_HEROES_FILE

    print("=" * 60, flush=True)
    print("  名将杀 Agent - 增量爬虫", flush=True)
    print("=" * 60, flush=True)
    mode = "预览" if args.dry_run else str(output_path)
    print(f"  输出: {mode}", flush=True)

    # 1. 获取官网全量数据
    print("\n[1/3] 获取官网数据...", flush=True)
    all_raw = fetch_all_raw()

    # 2. 筛选目标
    print("\n[2/3] 筛选目标武将...", flush=True)
    target_raw = list(all_raw)

    if args.incremental:
        existing_ids = load_existing_ids(output_path)
        target_raw = incremental_collect(all_raw, existing_ids)
        print(f"  增量目标: {len(target_raw)} 个武将要处理", flush=True)

    if args.hero:
        names = [n.strip() for n in args.hero.split(",") if n.strip()]
        filtered = filter_by_names(all_raw, names)
        print(f"  按名称筛选: {names} -> 匹配 {len(filtered)} 条", flush=True)
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
                logger.warning("无效 ID: %s", hid)
        filtered = filter_by_ids(all_raw, ids)
        print(f"  按 ID 筛选: {sorted(ids)} -> 匹配 {len(filtered)} 条", flush=True)
        if args.incremental:
            target_ids = {r.get("id") for r in target_raw}
            filtered = [r for r in filtered if r.get("id") in target_ids]
        target_raw = filtered

    if not target_raw:
        print("\n  无目标武将，退出。", flush=True)
        return

    # 3. 清洗 + 校验 + 输出
    print("\n[3/3] 清洗与写入...", flush=True)

    # 指定武将模式（不含 --incremental）：先删旧数据再写入新数据
    replace_ids = None
    if (args.hero or args.hero_id) and not args.incremental:
        replace_ids = {r["id"] for r in target_raw if r.get("id") is not None}

    run(target_raw, output_path, dry_run=args.dry_run,
        append=args.incremental, replace_ids=replace_ids,
        skip_images=args.skip_images)

    print(f"\n{'=' * 60}", flush=True)
    print(f"  完成!", flush=True)
    print(f"{'=' * 60}", flush=True)


if __name__ == "__main__":
    main()
