"""
名将杀 Agent - 官网爬虫 + 数据清洗

数据采集流程：
  1. 获取官网页面，定位 JS chunk
  2. 下载 JS chunk，提取 const e=[...] 数组
  3. JS 语法 → JSON 解析
  4. 字段映射与数据清洗
  5. Pydantic 模型校验
  6. 输出 JSON

清洗要点（详见 docs/field_mapping.md）：
  - skill_desc：HTML → 纯文本，拆分技能描述/结算/典故/设计思路段落
  - gender：int(1/2) → Gender 枚举
  - p_blood_max / p_card_max：str → int
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.scraper.crawler import (
    BAIKE_URL,
    fetch,
    find_chunk_url,
    extract_js_array,
    js_to_json,
    transform,
    validate_heroes,
)

logger = logging.getLogger(__name__)

# 默认输出路径
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent.parent / "data" / "heroes.json"


def crawl(dry_run: bool = False, output_path: str | None = None) -> None:
    """执行官网爬虫完整流程"""
    out_path = Path(output_path) if output_path else DEFAULT_OUTPUT

    print("=" * 60, flush=True)
    print("  名将杀 Agent - 官网武将采集", flush=True)
    print("=" * 60, flush=True)

    label = "预览模式" if dry_run else str(out_path)
    print(f"  输出: {label}", flush=True)

    try:
        # [1/5] 定位 JS chunk
        print("\n[1/5] 定位数据源...", flush=True)
        html = fetch(BAIKE_URL)
        chunk_url = find_chunk_url(html)
        print(f"  -> {chunk_url}", flush=True)

        # [2/5] 下载 JS
        print("\n[2/5] 下载 JS chunk...", flush=True)
        js_text = fetch(chunk_url)
        print(f"  大小: {len(js_text):,} 字节", flush=True)

        # [3/5] 解析原始数据
        print("\n[3/5] 解析原始数据...", flush=True)
        raw_list = js_to_json(extract_js_array(js_text))
        print(f"  原始条数: {len(raw_list)}", flush=True)

        # [4/5] 清洗与映射
        print("\n[4/5] 数据清洗与字段映射...", flush=True)
        transformed = [transform(r) for r in raw_list if transform(r)]
        print(f"  清洗后: {len(transformed)} 条", flush=True)

        # 统计势力分布
        factions = {}
        for h in transformed:
            f = h["faction"] or "未知"
            factions[f] = factions.get(f, 0) + 1
        print("\n  势力分布:", flush=True)
        for f, c in sorted(factions.items(), key=lambda x: -x[1]):
            print(f"    {f}: {c}", flush=True)

        # [5/5] Pydantic 校验
        print("\n[5/5] Pydantic 模型校验...", flush=True)
        validated = validate_heroes(transformed)
        print(f"  校验通过: {len(validated)} 条", flush=True)

        skipped = len(transformed) - len(validated)
        if skipped:
            print(f"  校验失败: {skipped} 条（详见日志）", flush=True)

        # 输出
        if dry_run:
            print("\n  [预览] 前 5 条:", flush=True)
            for h in validated[:5]:
                sk = ", ".join(s["name"] for s in h["skills"])
                print(f"    ID={h['id']:>3}  {h['name']}  [{h['faction']}]  {sk}", flush=True)
            print(f"\n  (使用 --output 或去除 --dry-run 写入文件)", flush=True)
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(validated, f, ensure_ascii=False, indent=2)
            print(f"\n  已保存: {out_path}", flush=True)

        print(f"\n{'=' * 60}", flush=True)
        print(f"  完成! 共 {len(validated)} 个武将", flush=True)
        print(f"{'=' * 60}", flush=True)

    except Exception as e:
        print(f"\n[错误] {e}", flush=True)
        logger.exception("采集失败")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="名将杀官网武将采集")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入文件")
    parser.add_argument("--output", "-o", type=str, help="输出文件路径")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    crawl(dry_run=args.dry_run, output_path=args.output)


if __name__ == "__main__":
    main()
