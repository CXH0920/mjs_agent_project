"""公告武将变更事件提取（extract_hero_changes / build_timeline_events）测试。"""

from __future__ import annotations

from types import SimpleNamespace

from src.data.announcement_manager import Announcement
from src.scraper.official_source.announcement import build_timeline_events, extract_hero_changes

# 结构对齐真实公告（如 8月13日 / 8月27日停服更新预告）：章节标题加粗、技能名下划线、
# 新增武将带属性行与 &nbsp; 空行
ADJUST_HTML = """
<p>将军们好：</p>
<h1><strong>【新增武将】</strong></h1>
<h2>东方朔</h2>
<p>——4体力 4手牌上限 西汉 普通</p>
<p>奏牍三千：回合开始时，你可以摸一张牌。</p>
<p>&nbsp;</p>
<h2>金日磾</h2>
<p>——6体力 3手牌上限 西汉 稀有</p>
<p>秺侯诛莽：登场，你可以选择一名其他角色。</p>
<h1><strong>【武将调整】</strong></h1>
<h2>贾诩（增强）</h2>
<p><u>算无遗策</u></p>
<p>修改前：你打出的战法牌无法被识破抵消。</p>
<p>修改后：你打出的战法牌无法被识破抵消；当战法牌即将对你生效时，你可以将1张牌当作识破打出。</p>
<h2>马钧（削弱）</h2>
<p><u>龙骨汲流</u></p>
<p>修改前:装备区里的牌均视为♣。</p>
<p>修改后:装备区里的♣牌均视为装备牌。</p>
<h1><strong>【新增活动】</strong></h1>
<p>1.丹青阁上新：动态皮肤上架。</p>
"""


def test_extract_new_hero_events_collect_skill_names():
    events = extract_hero_changes("停服更新预告", ADJUST_HTML)
    new_events = [e for e in events if e["change_type"] == "新增"]
    assert [(e["hero"], e["skills"]) for e in new_events] == [
        ("东方朔", ["奏牍三千"]),
        ("金日磾", ["秺侯诛莽"]),
    ]


def test_extract_adjust_events_with_before_after():
    events = extract_hero_changes("停服更新预告", ADJUST_HTML)
    by_hero = {e["hero"]: e for e in events}
    jiaxu = by_hero["贾诩"]
    assert jiaxu["change_type"] == "增强"
    assert jiaxu["skills"] == [{
        "skill": "算无遗策",
        "before": "你打出的战法牌无法被识破抵消。",
        "after": "你打出的战法牌无法被识破抵消；当战法牌即将对你生效时，你可以将1张牌当作识破打出。",
    }]


def test_extract_adjust_events_supports_halfwidth_colon():
    events = extract_hero_changes("停服更新预告", ADJUST_HTML)
    majun = next(e for e in events if e["hero"] == "马钧")
    assert majun["change_type"] == "削弱"
    assert majun["skills"][0]["before"] == "装备区里的牌均视为♣。"
    assert majun["skills"][0]["after"] == "装备区里的♣牌均视为装备牌。"


def test_extract_summary_format_and_degraded_hero_level():
    """兼容 A 类"技能名：描述"摘要行；无法解析技能明细时保留 hero 级事件。"""
    html = """
    <p>【武将调整】</p>
    <p>白起（削弱）</p>
    <p>出奇无穷：打出牌→打出行动牌</p>
    <p>王翦（增强）</p>
    <p>本次调整细节略。</p>
    """
    events = extract_hero_changes("公告", html)
    by_hero = {e["hero"]: e for e in events}
    assert by_hero["白起"]["skills"] == [{"skill": "出奇无穷", "change": "打出牌→打出行动牌"}]
    assert by_hero["王翦"]["change_type"] == "增强"
    assert by_hero["王翦"]["skills"] == []


def test_extract_summary_line_with_halfwidth_colon():
    """摘要行用半角冒号时不崩溃，变更描述正常提取（此前固定按全角切会 IndexError）。"""
    html = """
    <p>【武将调整】</p>
    <p>法正（调整）</p>
    <p>奇画策算:限制为出牌阶段触发</p>
    <p>修改前：出牌阶段摸2张牌</p>
    <p>修改后：出牌阶段摸1张牌</p>
    """
    events = extract_hero_changes("公告", html)
    assert events[0]["skills"] == [{
        "skill": "奇画策算",
        "change": "限制为出牌阶段触发",
        "before": "出牌阶段摸2张牌",
        "after": "出牌阶段摸1张牌",
    }]


def test_build_timeline_events_filters_by_cutoff_and_flag():
    announcements = [
        {"id": 226, "title": "8月27日停服更新预告", "content": ADJUST_HTML,
         "url": "https://x/226", "publishdate": "2026-08-26 20:00:00", "hero_related": True},
        {"id": 200, "title": "8月10日停服更新预告", "content": ADJUST_HTML,
         "url": "https://x/200", "publishdate": "2026-08-09 20:00:00", "hero_related": True},
        {"id": 228, "title": "在线更新通知", "content": ADJUST_HTML,
         "url": "https://x/228", "publishdate": "2026-08-28 19:00:00", "hero_related": False},
    ]
    events = build_timeline_events(announcements, cutoff_date="2026-08-25")
    # 仅 226 入选：200 早于 cutoff（视为 A 类快照已覆盖），228 非武将相关
    assert {e["ref"] for e in events} == {"https://x/226"}
    assert all(e["date"] == "2026-08-26" and e["source"] == "announcement" for e in events)
    assert all(e["announcement_title"] == "8月27日停服更新预告" for e in events)


def test_build_timeline_events_accepts_announcement_models():
    announcement = Announcement(
        title="8月27日停服更新预告", content=ADJUST_HTML,
        publishdate="2026-08-26 20:00:00", hero_related=True,
    )
    events = build_timeline_events([announcement], cutoff_date="")
    heroes = {e["hero"] for e in events}
    assert heroes == {"东方朔", "金日磾", "贾诩", "马钧"}
    # url 为空时回退 id:N 作为 ref
    assert all(e["ref"] == "id:0" for e in events)


def test_build_timeline_events_supports_namespace_objects():
    announcement = SimpleNamespace(
        id=1, title="t", content=ADJUST_HTML, url="",
        publishdate="2026-09-01 10:00:00", hero_related=True,
    )
    events = build_timeline_events([announcement], cutoff_date="2026-08-25")
    assert events and events[0]["ref"] == "id:1"
