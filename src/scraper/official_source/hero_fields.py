"""官网武将数据的字段清洗、字段映射与模型校验（纯函数、无网络与文件 IO）。

从 crawler.py 分两步拆出：先是 clean_html/carry_over_local_fields，本次补齐
split_skill_desc/transform/validate_heroes（原拆分只走了一半），crawler 自此
专注抓取链路；各消费方经 crawler 的兼容 re-export 调用不受影响。
"""

from __future__ import annotations

import html as html_module
import json
import logging
import re
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# 本地扩展字段：官网不提供、由用户手工维护，采集时不得覆盖。
# hero_content_hash 同样排除这三项（见 announcement.py），二者口径必须一致。
LOCAL_EXTENSION_FIELDS = ("title", "difficulty", "mode_viability")

# Gender 映射表
GENDER_MAP = {1: "男", 2: "女"}

# 技能描述段落标题
SKILL_SECTION_TITLES = ["技能描述", "结算详情", "结算详解", "技能详解", "技能详情", "技能典故", "设计思路"]

# 「像标签」序列：< 后必须是字母、/字母、!-- 或 !；`<5`、`->` 不匹配故保留为正文。
_TAG_LIKE_RE = re.compile(r"<(?:/?[a-zA-Z][^>]*|!--.*?--|![^>]*)>", re.DOTALL)


def _unescape_and_strip_tags(text: str, rounds: int = 3) -> str:
    """解码实体并剥离随之显形的标签，交替多轮以覆盖 `&amp;lt;` 多层编码。"""
    for _ in range(rounds):
        previous = text
        text = _TAG_LIKE_RE.sub("", html_module.unescape(text))
        if text == previous:
            break
    return text


def clean_html(html_text: str | None) -> str:
    """剥离 HTML 标签并解码实体，返回纯文本（可直接展示，不可当 HTML 用）。

    解码后必须再剥一轮：官网正文里的 `&lt;`（如「手牌上限&lt;5」）会解码成
    字面 `<`；但第二轮只认「像标签」的序列，否则 `<5`、`->` 这类正文比较符
    会被当标签吞掉（实测 192 名武将有 11 条描述含此类符号）。
    """
    if not html_text:
        return ""
    text = str(html_text)
    text = _unescape_and_strip_tags(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\s+", " ", text).strip()


def carry_over_local_fields(new_heroes: list[dict], existing: list[dict] | None) -> list[dict]:
    """把旧数据里的本地扩展字段按 id 回填到本次采集结果上。

    官网只提供基础资料，title/difficulty/mode_viability 是用户在界面上手工
    维护的本地数据。旧数据缺失或字段不存在时保留模型默认值。
    """
    if not existing:
        return new_heroes

    previous_by_id = {
        int(hero["id"]): hero
        for hero in existing
        if isinstance(hero, dict) and hero.get("id") is not None
    }
    for hero in new_heroes:
        previous = previous_by_id.get(int(hero["id"]))
        if previous is None:
            continue
        for field in LOCAL_EXTENSION_FIELDS:
            if field in previous:
                hero[field] = previous[field]
    return new_heroes


# ============================================================
# 技能描述拆段
# ============================================================


def split_skill_desc(raw_desc: str | None) -> dict[str, str]:
    """
    先按 HTML 段落结构拆分原始 HTML，再逐段清洗。
    描述取值按 技能描述 -> 技能详情 -> 技能详解 顺序认领首个命中
    （官网人工编辑，标题措辞不统一）；结算详情/结算详解 -> settlement
    丢弃：技能典故、设计思路
    """
    if not raw_desc:
        return {"description": "", "settlement": ""}

    text = str(raw_desc)
    # 预处理：合并相邻的 <strong> 标签
    text = re.sub(r"</strong>\s*<strong>", "", text)

    # 构建段落标题正则；<strong> 按可选处理——官网标题偶尔漏包加粗（裸 <p>标题</p>）
    titles = "|".join(re.escape(t) for t in SKILL_SECTION_TITLES)
    section_pattern = re.compile(
        rf"<p>(?:<[^>]+>)*\s*(?:<strong>\s*)?({titles})(?:\s*</strong>)?(?:<[^>]+>)*\s*</p>",
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

    # 获取描述和结算；结算跳过已被描述认领的段落，避免同一段文字两处重复
    description = ""
    desc_key = None
    for key in ["技能描述", "技能详情", "技能详解"]:
        if key in sections:
            desc_key = key
            description = sections[key]
            break

    settlement = ""
    for key in ["结算详情", "结算详解", "技能详解", "技能详情"]:
        if key != desc_key and key in sections:
            settlement = sections[key]
            break

    return {"description": description, "settlement": settlement}


# ============================================================
# 字段映射
# ============================================================


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
        "faction": clean_html(raw.get("dynasty", "")),
        "position": clean_html(raw.get("p_positioning", "")),
        "max_hp": max_hp,
        "max_hand": max_hand,
        "gender": gender,
        "skills": skills,
        "icon_url": str(raw.get("icon_url", "")),
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
