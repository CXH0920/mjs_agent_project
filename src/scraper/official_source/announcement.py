"""官方公告采集与百科逐武将 diff 分析。

- 公告数据源：ucmsv2api.ztgame.com/api/news/list（公开 JSON API，单页 5 条含全文）
- 分类判据：仅正文 `【新增武将】/【武将调整】` 等章节标题，不按正文提及的武将名判定
- 延迟应对：拉取百科 JS chunk 逐武将计算内容哈希，与快照 diff 确认“什么真的变了”
"""

from __future__ import annotations

import hashlib
import html as html_module
import json
import logging
import re
import unicodedata
from typing import Any
from urllib.parse import urlencode, urljoin

from src.data.announcement_manager import AnnouncementStatus
from src.data.hero_timeline import load_timeline, normalize_change_type
from src.scraper.official_source.crawler import (
    BASE_URL,
    clean_html,
    fetch,
    fetch_all_raw,
    transform,
    validate_heroes,
)

logger = logging.getLogger(__name__)

ANNOUNCEMENT_API_URL = "https://ucmsv2api.ztgame.com/api/news/list"
ANNOUNCEMENT_PAGE_URL = f"{BASE_URL}/news/notice-1.html"
ANNOUNCEMENT_PER_PAGE = 5

# 章节标题判定：只有这些章节内的武将才属于“武将相关”
NEW_SECTION_NAMES = ("新增武将",)
ADJUST_SECTION_NAMES = ("武将调整", "武将加强", "武将削弱", "武将修改")
SECTION_HEADER_RE = re.compile(r"^【\s*([^】]+?)\s*】\s*$")
CHANGE_RE = re.compile(r"^(.+?)[（(](增强|削弱|调整|加强|修改|新增)[）)]\s*$")
# 新增武将章节内，独立成行的短名称（2-8 个中文/间隔号字符）视为新武将名
NEW_HERO_LINE_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff·]{2,8}$")


class AnnouncementFetchError(RuntimeError):
    """公告拉取（API 与 HTML 回退均失败）错误。"""


# ============================================================
# HTML → 结构化文本
# ============================================================


def _html_to_lines(html_text: str | None) -> list[str]:
    """去标签后按块保留换行，供章节提取使用。"""
    if not html_text:
        return []
    text = str(html_text)
    text = re.sub(
        r"<(br\s*/?|/p|/h[1-6]|/li|/div|/ul|/ol)>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html_module.unescape(text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return lines


def _extract_section(lines: list[str], section_names: tuple[str, ...]) -> list[str]:
    """取某个章节标题之后、下一个章节标题之前的行。"""
    start = None
    for index, line in enumerate(lines):
        match = SECTION_HEADER_RE.match(line)
        if match and match.group(1) in section_names:
            start = index
            break
    if start is None:
        return []
    content = []
    for line in lines[start + 1 :]:
        if SECTION_HEADER_RE.match(line):
            break
        content.append(line)
    return content


# ============================================================
# 公告解析
# ============================================================


def parse_announcement_list(raw: object) -> list[dict]:
    """将公告 API 响应解析为 Announcement 所需的原始字段列表。"""
    if not isinstance(raw, dict):
        raise ValueError("公告响应不是 JSON 对象")
    data = raw.get("data")
    if not isinstance(data, dict):
        raise ValueError("公告响应缺少 data")
    items = data.get("list")
    if not isinstance(items, list):
        raise ValueError("公告响应缺少 list")
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        announcement_id = item.get("id")
        title = clean_html(item.get("title", ""))
        if announcement_id is None or not title:
            continue
        default_url = str(item.get("defaulturl") or "")
        if default_url.startswith("http"):
            url = default_url
        elif default_url:
            url = urljoin(f"{BASE_URL}/news/", default_url)
        else:
            url = ""
        result.append({
            "id": int(announcement_id),
            "title": title,
            "content": str(item.get("content") or ""),
            "url": url,
            "publishdate": str(item.get("publishdate") or ""),
        })
    return result


def _parse_notice_page_html(html_text: str) -> list[dict]:
    """公告 API 不可用时的回退：解析 notice-1.html 的列表条目（无正文）。"""
    pattern = re.compile(
        r'<li>\s*<span class="news-icon">[^<]*</span>\s*'
        r'<a href="([^"]+)"[^>]*>\s*<strong class="news-stro-title">(.*?)</strong>'
        r"\s*<div class=\"divmsg\">.*?</div>\s*<span class=\"time\">(.*?)</span>",
        re.DOTALL,
    )
    items = []
    for href, title, publishdate in pattern.findall(html_text):
        url = urljoin(f"{BASE_URL}/news/", href) if not href.startswith("http") else href
        items.append({
            "id": 0,
            "title": clean_html(title),
            "content": "",
            "url": url,
            "publishdate": clean_html(publishdate),
            "content_missing": True,
        })
    if not items:
        raise ValueError("公告列表页未解析到条目")
    return items


def fetch_latest_announcements() -> list[dict]:
    """获取最近公告；API 失败时回退解析公告列表页 HTML。"""
    try:
        query = urlencode({
            "site": "mjs",
            "type": "notice",
            "page": "1",
            "per_page": str(ANNOUNCEMENT_PER_PAGE),
        })
        text = fetch(f"{ANNOUNCEMENT_API_URL}?{query}")
        return parse_announcement_list(json.loads(text))
    except Exception as api_error:
        logger.warning("公告 API 请求失败，尝试回退 HTML 解析: %s", api_error)
        try:
            return _parse_notice_page_html(fetch(ANNOUNCEMENT_PAGE_URL))
        except Exception as fallback_error:
            raise AnnouncementFetchError(
                f"公告获取失败（API: {api_error}; 回退: {fallback_error}）"
            ) from api_error


# ============================================================
# 武将相关判定
# ============================================================


def classify_hero_related(
    title: str,
    content_html: str,
    hero_names: set[str] | list[str],
) -> tuple[bool, list[dict]]:
    """按章节标题判定公告是否与武将调整/新增相关，并提取章节内武将。

    返回 (hero_related, matched_heroes)，其中 matched_heroes 元素为
    {name, change, known}；known=False 表示不在本地武将名单（未收录）。
    """
    lines = _html_to_lines(content_html)
    if not lines:
        lines = _html_to_lines(title)
    known_names = set(hero_names or [])

    matched: list[dict] = []
    seen: set[str] = set()

    def add(name: str, change: str) -> None:
        name = name.strip()
        if not name or name in seen:
            return
        seen.add(name)
        matched.append({"name": name, "change": change, "known": name in known_names})

    new_section = _extract_section(lines, NEW_SECTION_NAMES)
    adjust_section = _extract_section(lines, ADJUST_SECTION_NAMES)

    for line in new_section:
        if NEW_HERO_LINE_RE.match(line):
            add(line, "新增")
    for line in adjust_section:
        match = CHANGE_RE.match(line)
        if match:
            add(match.group(1), match.group(2))
        elif line in known_names:
            add(line, "调整")

    hero_related = bool(new_section) or bool(adjust_section)
    return hero_related, matched


# ============================================================
# 武将变更事件提取（供时间轴 data/mjs_adjustments.json 持久化）
# ============================================================

# 调整章节内的技能名行：整行短名称（无冒号），其后通常跟 修改前/修改后
SKILL_NAME_LINE_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff·]{2,10}$")


def extract_hero_changes(title: str, content_html: str | None) -> list[dict]:
    """从公告正文提取武将变更事件（hero + change_type + skills）。

    - 调整类章节：`武将名（类型）`行开启武将块；整行短名称为技能名，其后
      `修改前：/修改后：`行捕获前后描述；`技能名：描述`行视为变更摘要（兼容 A 类格式）；
    - 新增类章节：独立短名称行为新武将，后续`技能名：描述`行收集登场技能名列表；
    - 解析不出技能明细时保留 hero 级事件（skills 为空），不丢变更。
    """
    lines = _html_to_lines(content_html)
    if not lines:
        lines = _html_to_lines(title)
    events: list[dict] = []
    events.extend(_extract_new_hero_events(_extract_section(lines, NEW_SECTION_NAMES)))
    for section_name in ADJUST_SECTION_NAMES:
        events.extend(_extract_adjust_events(_extract_section(lines, (section_name,))))
    return events


def _extract_new_hero_events(section_lines: list[str]) -> list[dict]:
    events: list[dict] = []
    current: dict | None = None
    for line in section_lines:
        if NEW_HERO_LINE_RE.match(line):
            current = {"hero": line, "change_type": "新增", "skills": []}
            events.append(current)
            continue
        if current is None:
            continue
        if line.startswith("——") or line.startswith("--"):
            continue  # 属性行（体力/手牌上限/势力/稀有度）
        name = _skill_line_name(line)
        if name:
            current["skills"].append(name)
    return events


def _extract_adjust_events(section_lines: list[str]) -> list[dict]:
    events: list[dict] = []
    current: dict | None = None
    skill: dict | None = None

    def flush_skill() -> None:
        nonlocal skill
        if current is not None and skill is not None and any(skill.values()):
            current["skills"].append(skill)
        skill = None

    for line in section_lines:
        match = CHANGE_RE.match(line)
        if match:
            flush_skill()
            current = {
                "hero": match.group(1).strip(),
                "change_type": normalize_change_type(match.group(2)),
                "skills": [],
            }
            events.append(current)
            continue
        if current is None:
            continue
        if line.startswith("修改前") or line.startswith("修改后"):
            field = "before" if line.startswith("修改前") else "after"
            value = ""
            for sep in ("：", ":"):
                if sep in line:
                    value = line.split(sep, 1)[1].strip()
                    break
            skill = skill or {"skill": ""}
            skill[field] = value
            continue
        if SKILL_NAME_LINE_RE.match(line):
            flush_skill()
            skill = {"skill": line}
            continue
        name = _skill_line_name(line)
        if name:
            flush_skill()
            # _skill_line_name 兼容全/半角冒号，取变更描述须按同一分隔符切分，
            # 固定按全角切会把半角冒号行切成单元素导致 IndexError
            change = ""
            for sep in ("：", ":"):
                if sep in line:
                    change = line.split(sep, 1)[1].strip()
                    break
            skill = {"skill": name, "change": change}
    flush_skill()
    return events


def _skill_line_name(line: str) -> str | None:
    """`技能名：描述`行返回技能名（2-10 字），其余返回 None。"""
    for sep in ("：", ":"):
        if sep in line:
            name = line.split(sep, 1)[0].strip()
            return name if 2 <= len(name) <= 10 else None
    return None


def build_timeline_events(announcements: list, cutoff_date: str | None = None) -> list[dict]:
    """将 hero_related 公告转为时间轴事件（date/hero/change_type/skills/ref）。

    cutoff_date 之前的公告视为已被 A 类快照覆盖，跳过以免初始化重复；
    缺省取时间轴的 init_source_last_updated，时间轴未初始化时为空串（全量收录，
    适配无快照的全新安装）。Announcement 模型与原始 dict 均可。
    """
    if cutoff_date is None:
        cutoff_date = str(load_timeline().get("init_source_last_updated") or "")
    events: list[dict] = []
    for announcement in announcements or []:
        if _ann_field(announcement, "hero_related") is not True:
            continue
        publish_date = str(_ann_field(announcement, "publishdate") or "")[:10]
        if not publish_date or publish_date <= cutoff_date:
            continue
        announcement_id = _ann_field(announcement, "id")
        ref = str(_ann_field(announcement, "url") or "") or f"id:{announcement_id}"
        title = str(_ann_field(announcement, "title") or "")
        for entry in extract_hero_changes(title, _ann_field(announcement, "content")):
            events.append({
                "date": publish_date,
                "hero": entry["hero"],
                "change_type": entry["change_type"],
                "skills": entry.get("skills") or [],
                "source": "announcement",
                "ref": ref,
                "announcement_title": title,
            })
    return events


def _ann_field(announcement: object, key: str):
    """兼容 Announcement 模型（属性访问）与导入脚本读到的原始 dict。"""
    if isinstance(announcement, dict):
        return announcement.get(key)
    return getattr(announcement, key, None)


# ============================================================
# 百科逐武将 diff
# ============================================================


def _normalize_text(value: Any) -> str:
    """规范化官网文本：去标签/HTML 解码/去空白/全半角统一。"""
    return unicodedata.normalize("NFKC", clean_html(value)).strip()


def hero_content_hash(hero: dict) -> str:
    """基于官网字段计算武将内容哈希（不含本地扩展字段）。"""
    skills = []
    for skill in hero.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        skills.append({
            "name": _normalize_text(skill.get("name", "")),
            "description": _normalize_text(skill.get("description", "")),
            "settlement": _normalize_text(skill.get("settlement", "")),
        })
    payload = {
        "name": _normalize_text(hero.get("name", "")),
        "faction": _normalize_text(hero.get("faction", "")),
        "position": _normalize_text(hero.get("position", "")),
        "max_hp": hero.get("max_hp"),
        "max_hand": hero.get("max_hand"),
        "gender": _normalize_text(hero.get("gender", "")),
        "skills": skills,
        "icon_url": _normalize_text(hero.get("icon_url", "")),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def build_hero_snapshot(heroes: list[dict]) -> dict[str, dict]:
    """将清洗后的武将列表转为 {id: {name, hash}} 快照结构。"""
    snapshot = {}
    for hero in heroes:
        hero_id = hero.get("id")
        if hero_id is None:
            continue
        snapshot[str(int(hero_id))] = {
            "name": str(hero.get("name", "")),
            "hash": hero_content_hash(hero),
        }
    return snapshot


def diff_heroes(
    current: dict[int, dict],
    baseline: dict[int, dict],
) -> dict[str, list[dict]]:
    """对比当前与基线快照，返回 {added, modified, removed} 武将清单。"""
    current_ids = set(current)
    baseline_ids = set(baseline)
    added = sorted(current_ids - baseline_ids)
    removed = sorted(baseline_ids - current_ids)
    modified = sorted(
        hero_id
        for hero_id in current_ids & baseline_ids
        if current[hero_id]["hash"] != baseline[hero_id]["hash"]
    )
    return {
        "added": [
            {"name": current[hero_id]["name"], "id": int(hero_id)} for hero_id in added
        ],
        "modified": [
            {"name": current[hero_id]["name"], "id": int(hero_id)} for hero_id in modified
        ],
        "removed": [
            {"name": baseline[hero_id]["name"], "id": int(hero_id)} for hero_id in removed
        ],
    }


def fetch_baike_heroes() -> list[dict] | None:
    """获取并清洗百科全部武将；失败返回 None（不中断公告检查）。"""
    try:
        raw_list = fetch_all_raw()
        transformed = []
        for raw in raw_list:
            hero = transform(raw)
            if hero is not None:
                transformed.append(hero)
        return validate_heroes(transformed)
    except Exception:
        logger.exception("百科武将数据获取失败")
        return None


# ============================================================
# 更新候选与字段级差异摘要（供“更新武将数据”确认流程使用）
# ============================================================

SUMMARY_LINE_LIMIT = 120


def _truncate_text(value, limit: int = SUMMARY_LINE_LIMIT) -> str:
    """截断差异摘要文本，避免列表过长。"""
    text = str(value or "").strip()
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _skill_by_name(skills: list[dict]) -> dict[str, dict]:
    """按技能名索引技能列表（同名去重，仅用于对比）。"""
    indexed = {}
    for skill in skills or []:
        if not isinstance(skill, dict):
            continue
        name = str(skill.get("name") or "").strip()
        if name:
            indexed[name] = skill
    return indexed


def format_hero_full_text(hero: dict) -> str:
    """将武将字段格式化为只读全文（用于确认对话框的本地/官网对比）。"""
    if not hero:
        return ""
    lines = [
        f"名称：{_normalize_text(hero.get('name', ''))}",
        f"势力：{_normalize_text(hero.get('faction', ''))}",
        f"定位：{_normalize_text(hero.get('position', ''))}",
        f"体力/手牌：{hero.get('max_hp')} / {hero.get('max_hand')}",
        f"性别：{_normalize_text(hero.get('gender', ''))}",
    ]
    for skill in hero.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        lines.append("")
        lines.append(f"【{_normalize_text(skill.get('name', ''))}】")
        description = _normalize_text(skill.get("description", ""))
        if description:
            lines.append(f"描述：{description}")
        settlement = _normalize_text(skill.get("settlement", ""))
        if settlement:
            lines.append(f"结算：{settlement}")
    return "\n".join(lines)


def hero_field_diff_summary(local: dict, official: dict) -> list[str]:
    """对比本地与官网武将的官网字段，返回中文差异摘要。

    仅对比 hero_content_hash 使用的官网字段；无差异返回空列表。
    """
    lines: list[str] = []

    def field_line(label: str, local_value, official_value) -> None:
        if _normalize_text(local_value) != _normalize_text(official_value):
            lines.append(
                f"{label}：本地「{_truncate_text(local_value)}」→ 官网「{_truncate_text(official_value)}」"
            )

    field_line("势力", local.get("faction", ""), official.get("faction", ""))
    field_line("定位", local.get("position", ""), official.get("position", ""))
    field_line("性别", local.get("gender", ""), official.get("gender", ""))
    if local.get("max_hp") != official.get("max_hp"):
        lines.append(f"体力上限：本地 {local.get('max_hp')} → 官网 {official.get('max_hp')}")
    if local.get("max_hand") != official.get("max_hand"):
        lines.append(f"手牌上限：本地 {local.get('max_hand')} → 官网 {official.get('max_hand')}")

    local_skills = _skill_by_name(local.get("skills") or [])
    official_skills = _skill_by_name(official.get("skills") or [])
    for name in sorted(local_skills):
        if name not in official_skills:
            lines.append(f"本地技能【{name}】在官网已不存在")
    for name in sorted(official_skills):
        if name not in local_skills:
            lines.append(f"官网新增技能：【{name}】")
        else:
            local_skill = local_skills[name]
            official_skill = official_skills[name]
            if _normalize_text(local_skill.get("description")) != _normalize_text(
                official_skill.get("description")
            ):
                lines.append(f"技能【{name}】：描述不一致")
            elif _normalize_text(local_skill.get("settlement")) != _normalize_text(
                official_skill.get("settlement")
            ):
                lines.append(f"技能【{name}】：结算说明不一致")
    return lines


def build_update_candidates(
    announcements: list,
    local_heroes: list[dict],
    official_heroes: list[dict] | None,
    diff: dict,
) -> list[dict]:
    """组装“更新武将数据”的确认候选。

    来源 = ready 公告解析出的武将 + diff 的 added/modified，按武将名去重。
    每个候选：{name, hero_id|None, change, source, known, summary[]}。
    official_heroes 为 None 时（官网获取失败）跳过差异摘要计算。
    """
    local_by_name: dict[str, dict] = {}
    local_by_id: dict[int, dict] = {}
    for hero in local_heroes:
        hero_id = hero.get("id")
        if hero_id is None:
            continue
        local_by_name[str(hero.get("name", ""))] = hero
        local_by_id[int(hero_id)] = hero

    official_by_name: dict[str, dict] = {}
    for hero in official_heroes or []:
        name = str(hero.get("name", ""))
        if name:
            official_by_name[name] = hero

    candidates: list[dict] = []
    seen: set[str] = set()

    def add(name: str, change: str, source: str, hero_id: int | None = None, known: bool = False) -> None:
        if not name:
            return
        if name in seen:
            # 已存在：仅补充后续来源（如 diff）提供的本地未知 ID
            for candidate in candidates:
                if candidate["name"] == name:
                    if candidate["hero_id"] is None and hero_id is not None:
                        candidate["hero_id"] = hero_id
                    break
            return
        seen.add(name)
        candidates.append({
            "name": name,
            "hero_id": hero_id,
            "change": change,
            "source": source,
            "known": known,
            "summary": [],
            "local_full": "",
            "official_full": "",
        })

    for announcement in announcements or []:
        if getattr(announcement, "status", None) is not AnnouncementStatus.READY:
            continue
        for change in announcement.matched_heroes or []:
            hero_id = None
            local = local_by_name.get(change.name)
            if local is not None:
                hero_id = int(local["id"])
            add(change.name, change.change, f"公告：{announcement.title}", hero_id=hero_id, known=change.known)

    for group in ("added", "modified"):
        for entry in diff.get(group) or []:
            name = str(entry.get("name") or "")
            if not name:
                continue
            hero_id = int(entry["id"]) if entry.get("id") is not None else None
            if group == "added":
                add(name, "新增", "百科 diff", hero_id=hero_id, known=False)
            else:
                local = local_by_id.get(hero_id) if hero_id is not None else None
                if local is None:
                    continue
                add(name, "调整", "百科 diff", hero_id=hero_id, known=True)

    if official_heroes is None:
        return candidates

    for candidate in candidates:
        local = local_by_id.get(candidate["hero_id"]) if candidate["hero_id"] is not None else None
        official = official_by_name.get(candidate["name"])
        if candidate["change"] == "新增":
            if official is not None:
                candidate["summary"] = [
                    f"官网新增：{candidate['name']}（本地未收录，ID {int(official['id'])}）"
                ]
                candidate["official_full"] = format_hero_full_text(official)
            continue
        if local is not None:
            candidate["local_full"] = format_hero_full_text(local)
        if official is not None:
            candidate["official_full"] = format_hero_full_text(official)
        if local is not None and official is not None:
            candidate["summary"] = hero_field_diff_summary(local, official)
    return candidates
