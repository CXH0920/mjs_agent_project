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
import html as html_module
import json
import logging
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================

BAIKE_URL = "https://mjs.ztgame.com/baike/"
BASE_URL = "https://mjs.ztgame.com"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent.parent / "data" / "heroes.json"

TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"
}

# Gender 映射表
GENDER_MAP = {1: "男", 2: "女"}

# 技能描述段落标题（用于拆分 HTML）
SKILL_SECTION_TITLES = ["技能描述", "结算详情", "结算详解", "技能详解", "技能详情", "技能典故", "设计思路"]


# ============================================================
# 网络请求
# ============================================================

def _fetch(url: str) -> str:
    """带重试机制的 HTTP GET 请求"""
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:
            logger.warning("请求失败 [%d/%d]: %s — %s", attempt, MAX_RETRIES, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                raise


# ============================================================
# JS chunk 解析
# ============================================================

def _find_chunk_url(html: str) -> str:
    """从官网首页找到 JS chunk URL"""
    m = re.search(r"/_nuxt/mjbk\.[a-f0-9]+\.js", html)
    if not m:
        raise RuntimeError("JS chunk 未找到")
    return BASE_URL + m.group()


def _extract_js_array(js_text: str) -> str:
    """提取 const e=[...] 数组的 JSON 文本"""
    s = js_text.find("const e=[")
    if s < 0:
        raise RuntimeError("const e=[ 未找到")
    start = js_text.index("[", s)
    depth = 0
    for i in range(start, len(js_text)):
        c = js_text[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return js_text[start : i + 1]
    raise RuntimeError("JS 数组未闭合")


def _js_to_json(text: str) -> list[dict]:
    """将 JS 对象数组转为 Python 列表"""
    # 对象 key 加引号
    text = re.sub(r'(?<=[{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'"\1":', text)
    # undefined → null
    text = re.sub(r":\s*undefined(?=[,}\]])", ":null", text)
    # 移除尾部多余逗号
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return json.loads(text)


# ============================================================
# 数据清洗
# ============================================================

def _clean_html(html_text: str | None) -> str:
    """剥离 HTML 标签，unescape，归一化空白"""
    if not html_text:
        return ""
    text = str(html_text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _split_skill_desc(raw_desc: str | None) -> dict[str, str]:
    """
    先按 HTML 段落结构拆分原始 HTML，再逐段清洗。
    只保留：技能描述 -> description，结算详情/结算详解 -> settlement
    丢弃：技能典故、设计思路
    拆分锚点：<p><strong>段落标题</strong></p>
    """
    if not raw_desc:
        return {"description": "", "settlement": ""}

    text = str(raw_desc)

    # 预处理：合并相邻的 <strong> 标签（如 <strong>结</strong><strong>算详解</strong>）
    text = re.sub(r"</strong>\s*<strong>", "", text)

    # 构建段落标题正则
    titles = "|".join(re.escape(t) for t in SKILL_SECTION_TITLES)
    section_pattern = re.compile(
        rf"<p>(?:<[^>]+>)*\s*<strong>\s*({titles})\s*</strong>(?:<[^>]+>)*\s*</p>",
        re.IGNORECASE,
    )

    # 第一步：按 HTML 结构拆分出各段落
    sections = {}
    current_title = None
    current_parts = []
    last_end = 0

    for m in section_pattern.finditer(text):
        if current_title:
            between = text[last_end:m.start()]
            if between.strip():
                current_parts.append(between)
            sections[current_title] = current_parts

        current_title = m.group(1).strip()
        current_parts = []
        last_end = m.end()

    if current_title and last_end < len(text):
        remaining = text[last_end:]
        if remaining.strip():
            current_parts.append(remaining)
        sections[current_title] = current_parts

    # 第二步：逐段清洗 HTML
    def _clean_html_parts(parts):
        result = []
        for part in parts:
            cleaned = re.sub(r"<[^>]+>", "", part)
            cleaned = html_module.unescape(cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                result.append(cleaned)
        return "\n".join(result)

    description = _clean_html_parts(sections.get("技能描述", []))
    # 结算部分存在多种历史命名，取非空的那个
    settlement_parts = (
        sections.get("结算详情")
        or sections.get("结算详解")
        or sections.get("技能详解")
        or sections.get("技能详情")
        or []
    )
    settlement = _clean_html_parts(settlement_parts)

    if not description:
        logger.warning("技能【技能描述】段落缺失: '%s'", str(raw_desc)[:80])

    return {"description": description, "settlement": settlement}
def _transform(raw: dict) -> dict | None:
    """
    将原始数据映射为模型字段格式。

    返回 dict（通过 Pydantic 校验后写入）或 None（关键字段缺失时跳过）。
    """
    hero_id = raw.get("id")
    if hero_id is None:
        logger.warning("跳过: 缺少 id 字段 — %s", raw.get("name", "?"))
        return None

    name = _clean_html(raw.get("name", ""))
    if not name:
        logger.warning("跳过 id=%s: 名称字段为空", hero_id)
        return None

    # 性别映射
    gender_raw = raw.get("gender")
    gender = GENDER_MAP.get(gender_raw, "男")  # 默认男

    # 体力/手牌上限（原始为字符串）
    try:
        max_hp = int(raw.get("p_blood_max", 4))
    except (ValueError, TypeError):
        max_hp = 4

    try:
        max_hand = int(raw.get("p_card_max", 4))
    except (ValueError, TypeError):
        max_hand = 4

    # 技能清洗
    skills: list[dict[str, Any]] = []
    raw_skills = raw.get("skill", [])
    if isinstance(raw_skills, list):
        for sk in raw_skills:
            if not isinstance(sk, dict):
                continue
            sk_name = _clean_html(sk.get("skill_name", ""))
            if not sk_name:
                continue
            sk_parts = _split_skill_desc(sk.get("skill_desc", ""))
            skills.append({
                "name": sk_name,
                "description": sk_parts["description"],
                "settlement": sk_parts["settlement"],
            })

    hero = {
        "id": hero_id,
        "name": name,
        "title": "",                           # 官网无此字段
        "faction": _clean_html(raw.get("dynasty", "")),
        "position": _clean_html(raw.get("p_positioning", "")),
        "max_hp": max_hp,
        "max_hand": max_hand,
        "gender": gender,
        "skills": skills,
        "difficulty": 2,                       # 官网无此字段，默认 MEDIUM
        "mode_viability": {},
        "last_updated": date.today().isoformat(),
    }
    return hero


# ============================================================
# Pydantic 校验
# ============================================================

def _validate_heroes(heroes: list[dict]) -> list[dict]:
    """通过 Pydantic Hero 模型校验，返回校验后的 dict 列表"""
    # 延迟导入，避免启动时依赖
    from src.data.models import Hero

    validated: list[dict] = []
    for h in heroes:
        try:
            obj = Hero.model_validate(h)
            validated.append(obj.model_dump(mode="json"))
        except Exception as e:
            logger.error("Pydantic 校验失败 id=%s (%s): %s", h.get("id"), h.get("name"), e)
            # 记录异常条目至日志，不清洗
            logger.info("异常数据: %s", json.dumps(h, ensure_ascii=False))
    return validated


# ============================================================
# 主流程
# ============================================================

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
        html = _fetch(BAIKE_URL)
        chunk_url = _find_chunk_url(html)
        print(f"  -> {chunk_url}", flush=True)

        # [2/5] 下载 JS
        print("\n[2/5] 下载 JS chunk...", flush=True)
        js_text = _fetch(chunk_url)
        print(f"  大小: {len(js_text):,} 字节", flush=True)

        # [3/5] 解析原始数据
        print("\n[3/5] 解析原始数据...", flush=True)
        raw_list = _js_to_json(_extract_js_array(js_text))
        print(f"  原始条数: {len(raw_list)}", flush=True)

        # [4/5] 清洗与映射
        print("\n[4/5] 数据清洗与字段映射...", flush=True)
        transformed = [_transform(r) for r in raw_list if _transform(r)]
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
        validated = _validate_heroes(transformed)
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
