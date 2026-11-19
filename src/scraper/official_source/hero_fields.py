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

# <br> 换行视同段边界：官网有「标题与正文同 <p>、中间 <br /> 换行」的录入形态；大小写不敏感兜 <BR>
_BR_SPLIT_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


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
    先按 </p> 切行、行内 <br> 切片，标题以纯文本等值匹配词表——判定不依赖标签形式，
    官网人工编辑的排版变体（<b> 包裹/漏包加粗/标题与正文同 <p> 以 <br /> 换行）不再
    导致描述丢失；行内无标题片时整行清洗，输出与历史行为逐字节一致（hero_content_hash
    纳入 description/settlement，文本差异会误报「武将资料更新」公告并使忽略名单失配）。
    描述取值按 技能描述 -> 技能详情 -> 技能详解 顺序认领首个命中
    （官网人工编辑，标题措辞不统一）；结算详情/结算详解 -> settlement
    丢弃：技能典故、设计思路
    首个标题前的正文攒入 prelude，描述认领全落空时兜底（漏写首标题不再静默丢正文）。
    """
    if not raw_desc:
        return {"description": "", "settlement": ""}

    text = str(raw_desc)
    # 预处理：合并相邻的 <strong> 标签
    text = re.sub(r"</strong>\s*<strong>", "", text)

    sections: dict[str, str] = {}
    prelude: list[str] = []
    current_title: str | None = None

    for line in text.split("</p>"):
        pieces = [clean_html(piece) for piece in _BR_SPLIT_RE.split(line)]
        if not any(piece in SKILL_SECTION_TITLES for piece in pieces):
            # 行内无标题片：整行清洗，与历史行为逐字节一致（正文段内 <br> 处不加空格）
            part = clean_html(line)
            if not part:
                continue
            if current_title is None:
                prelude.append(part)
            else:
                sections[current_title] = (sections[current_title] + " " + part).strip()
            continue
        for piece in pieces:
            if not piece:
                continue
            if piece in SKILL_SECTION_TITLES:
                current_title = piece
                sections[current_title] = ""
            elif current_title is None:
                prelude.append(piece)
            else:
                sections[current_title] = (sections[current_title] + " " + piece).strip()

    # 空正文段不参与认领：「技能描述」标题下无正文时，认领链继续落到「技能详情」
    sections = {key: value for key, value in sections.items() if value}

    # 没有标题的情况：整段作为描述
    if not sections:
        return {"description": clean_html(text), "settlement": ""}

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

    # 描述认领全落空：用首个标题前攒下的正文兜底
    if not description and prelude:
        description = " ".join(prelude)

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
            sk_desc_raw = sk.get("skill_desc", "")
            sk_parts = split_skill_desc(sk_desc_raw)
            # 原文有正文但描述解析为空：疑似词表外新排版，告警定位到武将与技能，
            # 避免只靠下游 Pydantic 校验报错倒查（clean_html 真值判断排除本就无描述的技能）
            if not sk_parts["description"] and clean_html(sk_desc_raw):
                logger.warning(
                    "技能描述解析为空（疑似官网新排版）: id=%s (%s) 技能=%s 原文=%.80s",
                    hero_id, name, sk_name, str(sk_desc_raw),
                )
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
