"""官网武将数据的字段清洗与本地扩展字段保护。

从 crawler.py 拆出：纯函数、无网络与文件 IO，便于单测且让 crawler 专注抓取链路。
"""

from __future__ import annotations

import html as html_module
import re

# 本地扩展字段：官网不提供、由用户手工维护，采集时不得覆盖。
# hero_content_hash 同样排除这三项（见 announcement.py），二者口径必须一致。
LOCAL_EXTENSION_FIELDS = ("title", "difficulty", "mode_viability")

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
