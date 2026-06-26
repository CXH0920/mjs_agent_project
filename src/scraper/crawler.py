"""
名将杀 Agent - 爬虫核心模块

提供官网数据采集、JS chunk 解析、数据清洗和校验的公共 API。
被 official.py 和 incremental.py 共同使用。
"""

from __future__ import annotations

import html as html_module
import json
import logging
import re
import time
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================

BAIKE_URL = "https://mjs.ztgame.com/baike/"
BASE_URL = "https://mjs.ztgame.com"

TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"
}

# Gender 映射表
GENDER_MAP = {1: "男", 2: "女"}

# 技能描述段落标题
SKILL_SECTION_TITLES = ["技能描述", "结算详情", "结算详解", "技能详解", "技能详情", "技能典故", "设计思路"]


# ============================================================
# 网络请求
# ============================================================


def fetch(url: str, binary: bool = False) -> str | bytes:
    """带重试机制的 HTTP GET 请求

    binary=True 时返回原始 bytes（用于下载图片等二进制资源），
    否则解码为 utf-8 字符串返回。
    """
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = resp.read()
                return data if binary else data.decode("utf-8")
        except Exception as e:
            logger.warning("请求失败 [%d/%d]: %s — %s", attempt, MAX_RETRIES, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                raise


# ============================================================
# JS chunk 解析
# ============================================================


def find_chunk_url(html: str) -> str:
    """从官网首页找到 JS chunk URL"""
    m = re.search(r"/_nuxt/mjbk\.[a-f0-9]+\.js", html)
    if not m:
        raise RuntimeError("JS chunk 未找到")
    return BASE_URL + m.group()


def extract_js_array(js_text: str) -> str:
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


def js_to_json(text: str) -> list[dict]:
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


def clean_html(html_text: str | None) -> str:
    """剥离 HTML 标签，unescape，归一化空白"""
    if not html_text:
        return ""
    text = str(html_text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_skill_desc(raw_desc: str | None) -> dict[str, str]:
    """
    先按 HTML 段落结构拆分原始 HTML，再逐段清洗。
    只保留：技能描述 -> description，结算详情/结算详解 -> settlement
    丢弃：技能典故、设计思路
    """
    if not raw_desc:
        return {"description": "", "settlement": ""}

    text = str(raw_desc)
    # 预处理：合并相邻的 <strong> 标签
    text = re.sub(r"</strong>\s*<strong>", "", text)

    # 构建段落标题正则
    titles = "|".join(re.escape(t) for t in SKILL_SECTION_TITLES)
    section_pattern = re.compile(
        rf"<p>(?:<[^>]+>)*\s*<strong>\s*({titles})\s*</strong>(?:<[^>]+>)*\s*</p>",
        re.IGNORECASE,
    )

    sections = {}
    current_title = None
    current_parts = []

    for line in text.split("</p>"):
        line = line.strip()
        if not line:
            continue
        # 检查是否为段落标题
        m = section_pattern.search(line + "</p>")
        if m:
            # 保存上一段
            if current_title and current_parts:
                sections[current_title] = " ".join(current_parts)
            current_title = m.group(1)
            current_parts = []
            # 标题后的内容
            rest = section_pattern.split(line + "</p>")[-1]
            if rest and rest != "</p>":
                cleaned = clean_html(rest)
                if cleaned:
                    current_parts.append(cleaned)
        else:
            cleaned = clean_html(line)
            if cleaned:
                current_parts.append(cleaned)

    # 保存最后一段
    if current_title and current_parts:
        sections[current_title] = " ".join(current_parts)

    # 没有标题的情况：整段作为描述
    if not sections:
        full = clean_html(text)
        return {"description": full, "settlement": ""}

    # 获取描述和结算
    description = sections.get("技能描述", "")
    settlement = ""

    for key in ["结算详情", "结算详解", "技能详解", "技能详情"]:
        if key in sections:
            settlement = sections[key]
            break

    return {"description": description, "settlement": settlement}


def transform(raw: dict) -> dict | None:
    """
    将原始数据映射为模型字段格式。
    返回 dict 或 None（关键字段缺失时跳过）。
    """
    hero_id = raw.get("id")
    if hero_id is None:
        logger.warning("跳过: 缺少 id 字段 — %s", raw.get("name", "?"))
        return None

    name = clean_html(raw.get("name", ""))
    if not name:
        logger.warning("跳过 id=%s: 名称字段为空", hero_id)
        return None

    # 性别映射
    gender_raw = raw.get("gender")
    gender = GENDER_MAP.get(gender_raw, "男")

    # 体力/手牌上限
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
            sk_name = clean_html(sk.get("skill_name", ""))
            if not sk_name:
                continue
            sk_parts = split_skill_desc(sk.get("skill_desc", ""))
            skills.append({
                "name": sk_name,
                "description": sk_parts["description"],
                "settlement": sk_parts["settlement"],
            })

    hero = {
        "id": hero_id,
        "name": name,
        "title": "",
        "faction": clean_html(raw.get("dynasty", "")),
        "position": clean_html(raw.get("p_positioning", "")),
        "max_hp": max_hp,
        "max_hand": max_hand,
        "gender": gender,
        "skills": skills,
        "icon_url": str(raw.get("icon_url", "")),
        "difficulty": 2,
        "mode_viability": {},
        "last_updated": date.today().isoformat(),
    }
    return hero


# ============================================================
# Pydantic 校验
# ============================================================


def validate_heroes(heroes: list[dict]) -> list[dict]:
    """通过 Pydantic Hero 模型校验，返回校验后的 dict 列表"""
    from src.data.models import Hero

    validated: list[dict] = []
    for h in heroes:
        try:
            obj = Hero.model_validate(h)
            validated.append(obj.model_dump(mode="json"))
        except Exception as e:
            logger.error("Pydantic 校验失败 id=%s (%s): %s", h.get("id"), h.get("name"), e)
            logger.info("异常数据: %s", json.dumps(h, ensure_ascii=False))
    return validated


# ============================================================
# 高阶流程：全量获取原始数据
# ============================================================


def fetch_all_raw() -> list[dict]:
    """从官网获取全部武将的原始数据（fetch page → find chunk → download → extract → parse）"""
    html = fetch(BAIKE_URL)
    chunk_url = find_chunk_url(html)
    print(f"  -> {chunk_url}", flush=True)
    js_text = fetch(chunk_url)
    raw_list = js_to_json(extract_js_array(js_text))
    print(f"  -> 官网原始数据: {len(raw_list)} 条", flush=True)
    return raw_list


# ============================================================
# 头像下载
# ============================================================

IMAGES_DIR = Path(__file__).resolve().parent.parent.parent / "images"


def download_hero_images(
    raw_list: list[dict],
    image_dir: str | Path | None = None,
    skip_existing: bool = True,
) -> int:
    """从原始 JS 数据中提取 icon_url 并将头像下载到本地

    Args:
        raw_list: 原始 JS chunk 数据（含 icon_url 字段）。
        image_dir: 输出目录，默认 project_root/images/。
        skip_existing: True 时跳过已存在的文件。

    Returns:
        成功下载的头像数量。
    """
    out_dir = Path(image_dir) if image_dir else IMAGES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for raw in raw_list:
        icon_url = raw.get("icon_url", "")
        if not icon_url:
            continue

        name = clean_html(raw.get("name", ""))
        if not name:
            continue

        # 解析扩展名
        parsed = urlparse(icon_url)
        ext = Path(parsed.path).suffix or ".png"

        dest = out_dir / f"{name}{ext}"

        if skip_existing and dest.exists():
            continue

        try:
            data = fetch(icon_url, binary=True)
            dest.write_bytes(data)
            count += 1
        except Exception as e:
            logger.warning("头像下载失败 %s: %s", name, e)

    return count
