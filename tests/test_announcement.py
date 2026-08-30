"""公告解析、武将相关判定、百科 diff 与公告服务测试。"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.business.announcement import announcement_service as service_module
from src.business.announcement.announcement_service import AnnouncementService
from src.data.announcement_manager import (
    Announcement,
    AnnouncementManager,
    AnnouncementStatus,
    BaikeSnapshot,
    HeroChange,
    load_baike_snapshot,
    save_baike_snapshot,
)
from src.data.hero_manager import HeroManager
from src.data.models import Hero
from src.scraper.official_source import announcement as announcement_module
from src.ui.app.main_window import MainWindow
from src.scraper.official_source.announcement import (
    build_hero_snapshot,
    build_update_candidates,
    classify_hero_related,
    diff_heroes,
    format_hero_full_text,
    hero_content_hash,
    hero_field_diff_summary,
    parse_announcement_list,
)

HERO_NAMES = {"贾诩", "马钧", "山涛", "王元姬", "羊祜"}

SAMPLE_API_RESPONSE = {
    "code": 0,
    "data": {
        "total": 146,
        "current_page": 1,
        "last_page": 30,
        "list": [
            {
                "id": 209,
                "title": "8月13日05：00停服更新预告",
                "content": "<p>将军们好：</p><p>以下为8月13日停服更新内容。</p>",
                "publishdate": "2026-08-12 18:27:50",
                "defaulturl": "008-1786-000d1-530470.html",
            },
            {
                "id": 208,
                "title": "8月10日21：35在线更新通知,",
                "content": "<p>将军们好：</p>",
                "publishdate": "2026-08-10 21:32:00",
                "defaulturl": "003-1786-000d0-530245.html",
            },
        ],
    },
}

ANNOUNCEMENT_813 = """
<h1><strong>【新增武将】</strong></h1>
<h2>东方朔</h2>
<p>——4体力 4手牌上限 西汉 普通</p>
<p>奏牍三千：回合开始时……</p>
<h1><strong>【武将调整】</strong></h1>
<h2>贾诩（增强）</h2>
<p><u>算无遗策</u></p>
<p>修改前：你打出的战法牌无法被识破抵消；你可以将任意战法牌当作识破打出。</p>
<p>修改后：你打出的战法牌无法被识破抵消；当战法牌即将对你生效时，你可以将1张牌当作识破打出。</p>
<h2>马钧（削弱）</h2>
<p><u>龙骨汲流</u></p>
<p>修改前：……</p>
<p>修改后：……</p>
<h1><strong>【新增活动】</strong></h1>
<p>1.丹青阁上新……</p>
"""

ANNOUNCEMENT_810_DUNGEON = """
<p>将军们好：</p>
<p>本次更新内容如下：</p>
<p>1.收藏-形象界面的倒计时信息……</p>
<p>3.修正司马衷何不食肉糜描述；</p>
<p>4.司马衷何不食肉糜给贾南风出牌阶段效果，调整为每回合限1次；</p>
"""

ANNOUNCEMENT_FIX_WITH_HERO_NAMES = """
<p>1.修复山涛八斗方醉技能……</p>
<p>9.修复山涛八斗方醉发动后半段效果时会触发王元姬烛奸抑势技能的问题。</p>
"""

ANNOUNCEMENT_ADJUST_UNKNOWN = """
<h1><strong>【武将调整】</strong></h1>
<h2>神秘武将（增强）</h2>
<p>修改前：……</p>
<p>修改后：……</p>
"""

ANNOUNCEMENT_ADJUST_NO_PAREN = """
<h1><strong>【武将调整】</strong></h1>
<h2>山涛</h2>
<p>修改前：……</p>
<p>修改后：……</p>
"""

NOTICE_PAGE_HTML = """
<li>
  <span class="news-icon">公告</span>
  <a href="/news/008-1786-000d1-530470.html" rel="noopener noreferrer">
    <strong class="news-stro-title">8月13日05：00停服更新预告</strong>
    <div class="divmsg">将军们好： 以下为8月13日停服更新内容</div>
    <span class="time">2026-08-12</span>
  </a>
</li>
<li>
  <span class="news-icon">公告</span>
  <a href="/news/003-1786-000d0-530245.html" rel="noopener noreferrer">
    <strong class="news-stro-title">8月10日21：35在线更新通知,</strong>
    <div class="divmsg">将军们好</div>
    <span class="time">2026-08-10</span>
  </a>
</li>
"""


def _hero(**overrides) -> dict:
    hero = {
        "id": 1,
        "name": "贾诩",
        "title": "",
        "faction": "魏",
        "position": "控制",
        "max_hp": 4,
        "max_hand": 4,
        "gender": "男",
        "difficulty": 2,
        "mode_viability": {},
        "last_updated": "2026-07-16",
        "icon_url": "https://siteres.ztgame.com/a.png",
        "skills": [
            {"name": "算无遗策", "description": "你打出的战法牌无法被识破抵消。", "settlement": ""},
        ],
    }
    hero.update(overrides)
    return hero


def _raw_announcement(announcement_id: int, url: str, **overrides) -> dict:
    raw = {
        "id": announcement_id,
        "title": "8月13日05：00停服更新预告",
        "content": ANNOUNCEMENT_813,
        "url": url,
        "publishdate": "2026-08-12 18:27:50",
    }
    raw.update(overrides)
    return raw


def _make_service(tmp_path):
    manager = AnnouncementManager(tmp_path / "announcements.json")
    heroes = HeroManager()
    heroes._items = {
        1: Hero.model_validate(_hero(id=1, name="贾诩")),
        2: Hero.model_validate(_hero(id=2, name="马钧")),
    }
    service = AnnouncementService(
        manager,
        heroes,
        snapshot_path=tmp_path / "baike_snapshot.json",
    )
    return service, manager


# ============================================================
# 公告解析
# ============================================================


def test_parse_announcement_list() -> None:
    items = parse_announcement_list(SAMPLE_API_RESPONSE)
    assert len(items) == 2
    assert items[0]["id"] == 209
    assert items[0]["url"] == "https://mjs.ztgame.com/news/008-1786-000d1-530470.html"
    assert items[1]["publishdate"] == "2026-08-10 21:32:00"


def test_parse_announcement_list_invalid() -> None:
    with pytest.raises(ValueError):
        parse_announcement_list({"code": 0, "data": {}})


def test_parse_notice_page_html_fallback() -> None:
    items = announcement_module._parse_notice_page_html(NOTICE_PAGE_HTML)
    assert len(items) == 2
    assert items[0]["title"] == "8月13日05：00停服更新预告"
    assert items[0]["url"] == "https://mjs.ztgame.com/news/008-1786-000d1-530470.html"
    assert items[0]["content"] == ""
    assert items[0]["content_missing"] is True


# ============================================================
# 武将相关判定
# ============================================================


def test_classify_813_hero_announcement() -> None:
    related, matched = classify_hero_related("8月13日05：00停服更新预告", ANNOUNCEMENT_813, HERO_NAMES)
    assert related is True
    by_name = {item["name"]: item for item in matched}
    assert by_name["东方朔"]["change"] == "新增"
    assert by_name["东方朔"]["known"] is False
    assert by_name["贾诩"]["change"] == "增强"
    assert by_name["贾诩"]["known"] is True
    assert by_name["马钧"]["change"] == "削弱"


def test_classify_dungeon_adjustment_not_related() -> None:
    related, matched = classify_hero_related(
        "8月10日21：35在线更新通知,", ANNOUNCEMENT_810_DUNGEON, HERO_NAMES
    )
    assert related is False
    assert matched == []


def test_classify_fix_mentioning_heroes_not_related() -> None:
    related, matched = classify_hero_related(
        "8月7日17：40在线更新通知", ANNOUNCEMENT_FIX_WITH_HERO_NAMES, HERO_NAMES
    )
    assert related is False
    assert matched == []


def test_classify_adjust_unknown_name() -> None:
    related, matched = classify_hero_related("停服更新预告", ANNOUNCEMENT_ADJUST_UNKNOWN, HERO_NAMES)
    assert related is True
    assert matched[0]["name"] == "神秘武将"
    assert matched[0]["change"] == "增强"
    assert matched[0]["known"] is False


def test_classify_adjust_known_without_paren() -> None:
    related, matched = classify_hero_related("停服更新预告", ANNOUNCEMENT_ADJUST_NO_PAREN, HERO_NAMES)
    assert related is True
    assert matched[0]["name"] == "山涛"
    assert matched[0]["change"] == "调整"
    assert matched[0]["known"] is True


# ============================================================
# 内容哈希与 diff
# ============================================================


def test_hero_content_hash_ignores_local_fields() -> None:
    base = _hero()
    local_edited = _hero(title="本地称号", mode_viability={"2v2": "T1"}, difficulty=3)
    assert hero_content_hash(base) == hero_content_hash(local_edited)


def test_hero_content_hash_normalizes_format_variations() -> None:
    plain = _hero(skills=[
        {"name": "算无遗策", "description": "可以将1张牌当作识破打出", "settlement": ""},
    ])
    styled = _hero(skills=[
        {"name": "<b>算无遗策</b>", "description": "可以将１张牌当作识破打出  ", "settlement": " "},
    ])
    assert hero_content_hash(plain) == hero_content_hash(styled)


def test_hero_content_hash_changes_on_skill_change() -> None:
    before = _hero()
    after = _hero(skills=[
        {"name": "算无遗策", "description": "修改后的描述", "settlement": ""},
    ])
    assert hero_content_hash(before) != hero_content_hash(after)


def test_build_hero_snapshot_and_diff() -> None:
    h1 = _hero(id=1, name="贾诩")
    h2 = _hero(id=2, name="马钧")
    h3 = _hero(id=3, name="东方朔")
    baseline = {int(k): v for k, v in build_hero_snapshot([h1, h2]).items()}

    current = {int(k): v for k, v in build_hero_snapshot([h1, h2, h3]).items()}
    diff = diff_heroes(current, baseline)
    assert [entry["id"] for entry in diff["added"]] == [3]
    assert diff["modified"] == []
    assert diff["removed"] == []

    modified = {int(k): v for k, v in build_hero_snapshot(
        [_hero(id=1, name="贾诩", position="输出"), h2, h3]
    ).items()}
    diff2 = diff_heroes(modified, baseline)
    assert [entry["id"] for entry in diff2["modified"]] == [1]

    removed = {int(k): v for k, v in build_hero_snapshot([h2]).items()}
    diff3 = diff_heroes(removed, baseline)
    assert [entry["id"] for entry in diff3["removed"]] == [1]


# ============================================================
# 公告管理器与快照
# ============================================================


def _hero_related_raw(url: str = "https://mjs.ztgame.com/news/008.html") -> dict:
    return {
        "id": 209,
        "title": "8月13日05：00停服更新预告",
        "content": "<p>x</p>",
        "url": url,
        "publishdate": "2026-08-12 18:27:50",
        "hero_related": True,
        "matched_heroes": [{"name": "贾诩", "change": "增强", "known": True}],
    }


def test_announcement_manager_merge_dedup_and_status(tmp_path) -> None:
    manager = AnnouncementManager(tmp_path / "announcements.json")
    raw = _hero_related_raw()
    new = manager.merge_new([raw])
    assert len(new) == 1
    assert new[0].status is AnnouncementStatus.PENDING
    assert manager.merge_new([raw]) == []

    other = {
        "id": 208,
        "title": "在线更新",
        "url": "https://mjs.ztgame.com/news/other.html",
        "publishdate": "2026-08-10",
        "hero_related": False,
    }
    manager.merge_new([other])
    statuses = {announcement.url: announcement.status for announcement in manager.list_all()}
    assert statuses[other["url"]] is AnnouncementStatus.APPLIED


def test_announcement_manager_baseline_first_run(tmp_path) -> None:
    manager = AnnouncementManager(tmp_path / "announcements.json")
    new = manager.merge_new([_hero_related_raw()], baseline=True)
    assert new == []
    assert manager.list_all()[0].status is AnnouncementStatus.APPLIED


def test_announcement_manager_status_transitions(tmp_path) -> None:
    manager = AnnouncementManager(tmp_path / "announcements.json")
    manager.merge_new([_hero_related_raw()])
    assert manager.mark_ready_if_updated({"modified": [{"name": "贾诩", "id": 1}]}) is True
    assert manager.list_all()[0].status is AnnouncementStatus.READY

    manager.merge_new([_hero_related_raw("https://mjs.ztgame.com/news/009.html")])
    # 名称不匹配时保持 pending
    assert manager.mark_ready_if_updated({"modified": [{"name": "山涛", "id": 2}]}) is False
    pending = [a for a in manager.list_all() if a.status is AnnouncementStatus.PENDING]
    assert len(pending) == 1

    manager.mark_applied()
    assert all(a.status is AnnouncementStatus.APPLIED for a in manager.list_all())


def test_baike_snapshot_roundtrip(tmp_path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot = BaikeSnapshot(checked_at="2026-08-13T10:00:00", heroes={"1": {"name": "贾诩", "hash": "abc"}})
    save_baike_snapshot(snapshot, snapshot_path)
    loaded = load_baike_snapshot(snapshot_path)
    assert loaded.heroes["1"].name == "贾诩"
    assert loaded.heroes["1"].hash == "abc"
    assert load_baike_snapshot(tmp_path / "missing.json").heroes == {}


# ============================================================
# 公告服务
# ============================================================


def _run_check(service) -> object:
    """按真实两阶段契约执行一次检查：worker 计算（_do_check）+ GUI 收尾（_finalize_check）。"""
    result = service._do_check()
    service._finalize_check(result)
    return result


def test_service_do_check_flow(tmp_path, monkeypatch) -> None:
    service, manager = _make_service(tmp_path)
    announcements = [
        _raw_announcement(208, "https://mjs.ztgame.com/news/old.html", title="8月10日在线更新"),
    ]
    baike = [_hero(id=1, name="贾诩"), _hero(id=2, name="马钧")]
    monkeypatch.setattr(service_module, "fetch_latest_announcements", lambda: announcements)
    monkeypatch.setattr(service_module, "fetch_baike_heroes", lambda: baike)

    # 首次检查：全部为基线，不提醒，并用本地 heroes.json 初始化百科基线
    result = _run_check(service)
    assert result.error is None
    assert result.new_announcements == []
    assert result.hero_related == []
    assert result.pending_count == 0
    baseline = load_baike_snapshot(tmp_path / "baike_snapshot.json")
    assert set(baseline.heroes) == {"1", "2"}

    # 第二次检查：出现新武将相关公告，百科未变 → pending
    announcements.append(
        _raw_announcement(209, "https://mjs.ztgame.com/news/new.html", content=ANNOUNCEMENT_813)
    )
    result2 = _run_check(service)
    assert len(result2.new_announcements) == 1
    assert len(result2.hero_related) == 1
    assert result2.pending_count == 1
    assert result2.ready_count == 0

    # 百科更新（马钧技能变化）→ pending 变 ready
    baike[1] = _hero(id=2, name="马钧", position="输出")
    result3 = _run_check(service)
    assert result3.ready_count == 1
    assert [entry["id"] for entry in result3.diff["modified"]] == [2]

    # 采集完成 → applied 且快照刷新，再次检查 diff 归零、不重复提醒
    service.mark_applied()
    assert manager.pending_count() == 0
    assert manager.ready_count() == 0
    result4 = _run_check(service)
    assert result4.ready_count == 0
    assert result4.diff["modified"] == []


def test_service_do_check_announcement_failure(tmp_path, monkeypatch) -> None:
    service, _manager = _make_service(tmp_path)

    def _raise():
        raise RuntimeError("网络失败")

    monkeypatch.setattr(service_module, "fetch_latest_announcements", _raise)
    result = _run_check(service)
    assert result.error is not None
    assert result.new_announcements == []
    assert result.diff == {"added": [], "modified": [], "removed": []}


def test_service_do_check_baike_failure_keeps_snapshot(tmp_path, monkeypatch) -> None:
    service, _manager = _make_service(tmp_path)
    snapshot_path = tmp_path / "baike_snapshot.json"
    save_baike_snapshot(
        BaikeSnapshot(checked_at="x", heroes={"1": {"name": "贾诩", "hash": "old"}}),
        snapshot_path,
    )
    monkeypatch.setattr(service_module, "fetch_latest_announcements", lambda: [])
    monkeypatch.setattr(service_module, "fetch_baike_heroes", lambda: None)
    result = _run_check(service)
    assert result.error is None
    assert result.baike_ok is False
    assert result.diff == {"added": [], "modified": [], "removed": []}
    assert load_baike_snapshot(snapshot_path).heroes["1"].hash == "old"


def test_service_do_check_skips_baseline_when_local_unavailable(tmp_path, monkeypatch) -> None:
    """本地 heroes 文件存在但列表为空时，不得用官网当前建基线（否则 diff 恒空）。"""
    heroes = HeroManager()  # 默认文件存在但未加载 → items 空
    service = AnnouncementService(
        AnnouncementManager(tmp_path / "announcements.json"),
        heroes,
        snapshot_path=tmp_path / "baike_snapshot.json",
    )
    monkeypatch.setattr(service_module, "fetch_latest_announcements", lambda: [])
    monkeypatch.setattr(service_module, "fetch_baike_heroes", lambda: [_hero(id=1, name="贾诩")])
    result = _run_check(service)
    assert result.error is None
    assert not (tmp_path / "baike_snapshot.json").exists()
    assert result.diff == {"added": [], "modified": [], "removed": []}


def test_service_do_check_uses_baike_baseline_when_no_local_file(tmp_path, monkeypatch) -> None:
    """本地 heroes 文件不存在（全新安装）时，以当前百科为基线。"""
    heroes = HeroManager(tmp_path / "missing_heroes.json")
    service = AnnouncementService(
        AnnouncementManager(tmp_path / "announcements.json"),
        heroes,
        snapshot_path=tmp_path / "baike_snapshot.json",
    )
    monkeypatch.setattr(service_module, "fetch_latest_announcements", lambda: [])
    monkeypatch.setattr(service_module, "fetch_baike_heroes", lambda: [_hero(id=1, name="贾诩")])
    result = _run_check(service)
    assert result.error is None
    snapshot = load_baike_snapshot(tmp_path / "baike_snapshot.json")
    assert "1" in snapshot.heroes


def test_update_no_candidates_shows_toast(monkeypatch) -> None:
    """无候选时点击更新给出明确 toast，而不是静默无反应。"""
    from src.ui.app import main_window as main_window_module

    toasts = []
    monkeypatch.setattr(
        main_window_module,
        "show_toast",
        lambda parent, message, **kwargs: toasts.append(message),
    )

    class _Heroes:
        def list_heroes(self):
            return []

    fake = SimpleNamespace(
        _fetch_service=SimpleNamespace(is_busy=False),
        _announcement_manager=SimpleNamespace(list_announcements=lambda: []),
        _last_announcement_diff={"added": [], "modified": [], "removed": []},
        _data=SimpleNamespace(heroes=_Heroes()),
        _status_label=SimpleNamespace(setText=lambda s: None),
    )
    fake._collect_update_candidates_base = lambda local_heroes, announcements: []
    MainWindow._update_hero_data_from_announcements(fake)
    assert toasts
    assert "没有需要更新" in toasts[0]


def test_service_check_now_busy_guard(tmp_path) -> None:
    service, _manager = _make_service(tmp_path)
    calls = []
    service.check_started.connect(lambda: calls.append(1))

    class _AliveThread:
        def is_alive(self) -> bool:
            return True

    service._thread = _AliveThread()
    assert service.check_now() is False
    assert calls == []


def test_service_check_now_cooldown(tmp_path, monkeypatch) -> None:
    service, _manager = _make_service(tmp_path)
    calls = []
    service.check_started.connect(lambda: calls.append(1))

    class _FakeThread:
        def __init__(self, target, args=(), daemon=True):
            self.target = target
            self.args = args
            self.started = False

        def is_alive(self) -> bool:
            return False

        def start(self) -> None:
            self.started = True

    monkeypatch.setattr(service_module.threading, "Thread", _FakeThread)

    # 冷却期内：拒绝且不发信号
    service._last_check_started_at = time.monotonic()
    assert service.cooldown_remaining > 0
    assert service.check_now() is False
    assert calls == []

    # 冷却结束后：允许启动
    service._last_check_started_at = time.monotonic() - (service_module.CHECK_COOLDOWN_SECONDS + 1)
    assert service.cooldown_remaining == 0
    assert service.check_now() is True
    assert calls == [1]


def test_main_window_check_announcements_shows_dialogs(monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda parent, title, text: messages.append((title, text)),
    )

    class _CooldownService:
        is_busy = False
        cooldown_remaining = 30

        def check_now(self):
            raise AssertionError("冷却期不应启动检查")

    MainWindow._check_announcements(SimpleNamespace(_announcement_service=_CooldownService()))
    assert len(messages) == 1
    assert messages[0][0] == "公告检查"
    assert "秒后再试" in messages[0][1]

    class _BusyService:
        is_busy = True
        cooldown_remaining = 0

        def check_now(self):
            raise AssertionError("忙碌时不应启动检查")

    messages.clear()
    MainWindow._check_announcements(SimpleNamespace(_announcement_service=_BusyService()))
    assert len(messages) == 1
    assert "正在进行" in messages[0][1]

    class _ReadyService:
        is_busy = False
        cooldown_remaining = 0

        def __init__(self):
            self.started = False

        def check_now(self):
            self.started = True
            return True

    messages.clear()
    ready = _ReadyService()
    MainWindow._check_announcements(SimpleNamespace(_announcement_service=ready))
    assert messages == []
    assert ready.started is True


def test_on_announcement_check_finished_hero_related_toast(monkeypatch) -> None:
    """回归：matched_heroes 是 HeroChange 模型对象，不能用下标访问。"""
    from src.business.announcement.announcement_service import AnnouncementCheckResult
    from src.data.announcement_manager import Announcement, HeroChange
    from src.ui.app import main_window as main_window_module

    toasts = []
    monkeypatch.setattr(
        main_window_module,
        "show_toast",
        lambda parent, message, **kwargs: toasts.append(message),
    )

    announcement = Announcement(
        id=211,
        title="新公告",
        url="https://mjs.ztgame.com/news/new.html",
        hero_related=True,
        matched_heroes=[
            HeroChange(name="贾诩", change="增强", known=True),
            HeroChange(name="东方朔", change="新增", known=False),
        ],
    )
    result = AnnouncementCheckResult(
        new_announcements=[announcement],
        hero_related=[announcement],
        pending_count=1,
        ready_count=0,
        diff={"added": [], "modified": [], "removed": []},
        baike_ok=True,
    )
    fake = SimpleNamespace(
        _last_announcement_diff={"added": [], "modified": [], "removed": []},
        _status_label=SimpleNamespace(setText=lambda s: None),
    )
    fake._refresh_announcement_banner = lambda: None
    fake._refresh_announcement_dialog = lambda: None
    fake._hide_progress = lambda: None

    MainWindow._on_announcement_check_finished(fake, result)
    assert toasts
    assert "贾诩" in toasts[0]
    assert "东方朔" in toasts[0]


def test_main_window_announcement_integration(monkeypatch) -> None:
    """全路径：横幅文案、对话框全文、更新联动（覆盖模型/字典混用点）。

    注意：不能给 MainWindow 传只有少量武将的 heroes 文件，否则会触发
    攻略/相性“缺失引用”的模态修复弹窗导致测试阻塞；这里使用默认数据文件。
    """
    from PySide6.QtWidgets import QApplication, QDialog
    from src.business.announcement.announcement_service import AnnouncementCheckResult
    from src.data.announcement_manager import Announcement, AnnouncementStatus, HeroChange
    from src.data.guide_manager import GuideManager
    from src.data.synergy_manager import SynergyManager
    from src.ui.app import main_window as main_window_module

    toasts = []
    monkeypatch.setattr(
        main_window_module,
        "show_toast",
        lambda parent, message, **kwargs: toasts.append(message),
    )

    app = QApplication.instance() or QApplication([])
    window = MainWindow(HeroManager(), SynergyManager(), GuideManager())
    window.show()
    app.processEvents()
    try:
        announcement = Announcement(
            id=209,
            title="8月13日停服更新预告",
            url="https://mjs.ztgame.com/news/008-1786-000d1-530470.html",
            content="<p>【武将调整】贾诩（增强）</p>",
            hero_related=True,
            status=AnnouncementStatus.READY,
            matched_heroes=[
                HeroChange(name="东方朔", change="新增", known=False),
                HeroChange(name="贾诩", change="增强", known=True),
            ],
        )
        window._announcement_manager._items[announcement.url] = announcement

        jia_id = window._data.heroes.get_hero_by_name("贾诩").id
        ma_id = window._data.heroes.get_hero_by_name("马钧").id
        window._on_announcement_check_finished(AnnouncementCheckResult(
            new_announcements=[],
            hero_related=[],
            pending_count=0,
            ready_count=1,
            diff={
                "added": [{"name": "东方朔", "id": 188}],
                "modified": [{"name": "贾诩", "id": jia_id}, {"name": "马钧", "id": ma_id}],
                "removed": [],
            },
            baike_ok=True,
        ))
        assert window._announcement_banner.isVisible()
        banner_text = window._announcement_banner.message_label.text()
        assert "东方朔（新增）·未收录" in banner_text
        assert "贾诩（增强）" in banner_text
        assert toasts

        window._open_announcement_dialog()
        app.processEvents()
        dialog = window._announcement_dialog
        assert dialog._list.count() == 1
        dialog._list.setCurrentRow(0)
        app.processEvents()
        full_text = dialog._content.toPlainText()
        assert "8月13日停服更新预告" in full_text
        assert "东方朔（新增）·未收录" in full_text

        calls = []
        monkeypatch.setattr(window._announcement_service, "mark_applied", lambda: None)

        class _FakeFetchService:
            is_busy = False

            def fetch_specific(self, hero_ids):
                calls.append(("specific", hero_ids))
                window._on_fetch_completed(True)  # 模拟子进程完成，驱动下一阶段

            def fetch_incremental(self):
                calls.append(("incremental", None))
                window._on_fetch_completed(True)

        window._fetch_service = _FakeFetchService()

        # 基础候选：公告 ready matched + diff added/modified 并集、去重
        base = window._collect_update_candidates_base(
            [hero.model_dump(mode="json") for hero in window._data.heroes.list_heroes()],
            window._announcement_manager.list_announcements(),
        )
        base_names = [candidate["name"] for candidate in base]
        assert "贾诩" in base_names and "东方朔" in base_names and "马钧" in base_names
        by_name = {candidate["name"]: candidate for candidate in base}
        assert by_name["贾诩"]["hero_id"] == jia_id
        assert by_name["东方朔"]["known"] is False
        assert by_name["马钧"]["source"] == "百科 diff"

        # 确认流程：勾选部分（调整+新增）→ 链式执行
        class _AcceptDialog:
            def __init__(self, candidates, parent=None):
                self.selected_ids = [jia_id, ma_id]
                self.update_new = True

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(main_window_module, "HeroUpdateConfirmDialog", _AcceptDialog)
        window._on_hero_update_prepared({"candidates": base, "official_ok": True})
        assert calls == [("specific", [jia_id, ma_id]), ("incremental", None)]

        # 全取消：不采集，但刷新快照（mark_applied）
        applied = []
        monkeypatch.setattr(window._announcement_service, "mark_applied", lambda: applied.append(1))

        class _AllUncheckedDialog:
            selected_ids = []
            update_new = False

            def __init__(self, candidates, parent=None):
                pass

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(main_window_module, "HeroUpdateConfirmDialog", _AllUncheckedDialog)
        calls.clear()
        window._pending_update_phases = None
        window._on_hero_update_prepared({"candidates": base, "official_ok": True})
        assert calls == []
        assert applied == [1]

        # 用户取消：不采集、不刷新
        class _RejectDialog:
            def __init__(self, candidates, parent=None):
                pass

            def exec(self):
                return QDialog.DialogCode.Rejected

        monkeypatch.setattr(main_window_module, "HeroUpdateConfirmDialog", _RejectDialog)
        applied.clear()
        window._on_hero_update_prepared({"candidates": base, "official_ok": True})
        assert calls == []
        assert applied == []

        # 无候选：不启动线程，直接提示
        window._announcement_manager._items.clear()
        window._last_announcement_diff = {"added": [], "modified": [], "removed": []}
        window._update_hero_data_from_announcements()
        assert "没有需要更新" in window._status_label.text()

        # pending 公告：横幅显示等待文案，但更新按钮仍可用（点击有反馈）
        pending_ann = Announcement(
            id=214,
            title="待生效公告",
            url="https://mjs.ztgame.com/news/pending.html",
            hero_related=True,
            status=AnnouncementStatus.PENDING,
            matched_heroes=[HeroChange(name="贾诩", change="增强", known=True)],
        )
        window._announcement_manager._items[pending_ann.url] = pending_ann
        window._refresh_announcement_banner()
        assert window._announcement_banner.isVisible()
        assert window._announcement_update_button.isEnabled()

        # 有候选：启动后台线程拉官网算差异
        class _FakeThread:
            def __init__(self, target, args=(), daemon=True):
                self.target = target
                self.args = args
                self.started = False

            def is_alive(self):
                return False

            def start(self):
                self.started = True

        monkeypatch.setattr(main_window_module.threading, "Thread", _FakeThread)
        window._announcement_manager._items[announcement.url] = announcement
        window._last_announcement_diff = {
            "added": [],
            "modified": [{"name": "贾诩", "id": jia_id}, {"name": "马钧", "id": ma_id}],
            "removed": [],
        }
        window._update_hero_data_from_announcements()
        assert window._hero_update_thread.started is True

        # 进度条生命周期：检查开始显示/结束隐藏；子进程进度驱动；完成隐藏
        window._on_announcement_check_started()
        assert window._progress_bar.isVisible()
        window._on_announcement_progress("正在获取百科数据...")
        assert "百科" in window._progress_bar.format()
        window._on_announcement_check_finished(AnnouncementCheckResult(baike_ok=True))
        assert not window._progress_bar.isVisible()
        window._on_fetch_progress(2, 5, "数据清洗")
        assert window._progress_bar.isVisible()
        assert window._progress_bar.maximum() == 5
        assert window._progress_bar.value() == 2
        window._on_fetch_completed(True)
        assert not window._progress_bar.isVisible()
        dialog.close()
    finally:
        window.close()
        app.processEvents()


# ============================================================
# 字段级差异摘要与更新候选
# ============================================================


def _hero_dict(id_: int, name: str, position: str = "控制", skills=None, **overrides) -> dict:
    hero = _hero(id=id_, name=name, position=position)
    hero["skills"] = skills if skills is not None else [
        {"name": "技能A", "description": "技能A的描述", "settlement": ""},
    ]
    hero.update(overrides)
    return hero


def test_hero_field_diff_summary_position() -> None:
    local = _hero_dict(167, "刘据", position="共计")
    official = _hero_dict(167, "刘据", position="控制")
    lines = hero_field_diff_summary(local, official)
    assert any("定位" in line and "共计" in line and "控制" in line for line in lines)


def test_hero_field_diff_summary_skill_description() -> None:
    local = _hero_dict(106, "曹丕", skills=[
        {"name": "嗣承魏武", "description": "受伤，随机获得1张牌。受伤，随机获得1张牌。", "settlement": ""},
    ])
    official = _hero_dict(106, "曹丕", skills=[
        {"name": "嗣承魏武", "description": "受伤，随机获得1张牌。", "settlement": ""},
    ])
    lines = hero_field_diff_summary(local, official)
    assert any("嗣承魏武" in line and "描述不一致" in line for line in lines)


def test_hero_field_diff_summary_new_skill() -> None:
    local = _hero_dict(1, "贾诩", skills=[{"name": "技能A", "description": "描述", "settlement": ""}])
    official = _hero_dict(1, "贾诩", skills=[
        {"name": "技能A", "description": "描述", "settlement": ""},
        {"name": "技能B", "description": "新技能", "settlement": ""},
    ])
    lines = hero_field_diff_summary(local, official)
    assert any("官网新增技能" in line and "技能B" in line for line in lines)


def test_hero_field_diff_summary_max_hp() -> None:
    local = _hero_dict(1, "贾诩", max_hp=4)
    official = _hero_dict(1, "贾诩", max_hp=5)
    lines = hero_field_diff_summary(local, official)
    assert any("体力上限" in line for line in lines)


def test_hero_field_diff_summary_no_diff() -> None:
    assert hero_field_diff_summary(_hero_dict(1, "贾诩"), _hero_dict(1, "贾诩")) == []


def test_hero_field_diff_summary_truncates() -> None:
    local = _hero_dict(1, "贾诩", position="长" * 200)
    official = _hero_dict(1, "贾诩", position="控制")
    lines = hero_field_diff_summary(local, official)
    assert lines
    assert len(lines[0]) <= 200
    assert "…" in lines[0]


def test_format_hero_full_text_contains_fields() -> None:
    text = format_hero_full_text(_hero_dict(167, "刘据", position="共计"))
    assert "刘据" in text
    assert "共计" in text
    assert "技能A" in text


def test_build_update_candidates_combines_announcement_and_diff() -> None:
    announcement = Announcement(
        id=209,
        title="8月13日停服更新预告",
        url="https://mjs.ztgame.com/news/008.html",
        hero_related=True,
        status=AnnouncementStatus.READY,
        matched_heroes=[
            HeroChange(name="贾诩", change="增强", known=True),
            HeroChange(name="东方朔", change="新增", known=False),
        ],
    )
    local = [_hero_dict(161, "贾诩"), _hero_dict(184, "马钧")]
    diff = {
        "added": [{"name": "东方朔", "id": 188}],
        "modified": [{"name": "马钧", "id": 184}],
        "removed": [],
    }
    candidates = build_update_candidates([announcement], local, None, diff)
    by_name = {candidate["name"]: candidate for candidate in candidates}
    assert list(by_name) == ["贾诩", "东方朔", "马钧"]  # 按名称去重
    assert by_name["贾诩"]["hero_id"] == 161
    assert by_name["贾诩"]["change"] == "增强"
    assert by_name["贾诩"]["source"].startswith("公告：")
    assert by_name["贾诩"]["known"] is True
    assert by_name["东方朔"]["hero_id"] == 188
    assert by_name["东方朔"]["known"] is False
    assert by_name["马钧"]["source"] == "百科 diff"
    assert by_name["马钧"]["hero_id"] == 184


def test_build_update_candidates_summary_with_official() -> None:
    local = [_hero_dict(167, "刘据", position="共计")]
    official = [_hero_dict(167, "刘据", position="控制")]
    diff = {"added": [], "modified": [{"name": "刘据", "id": 167}], "removed": []}
    candidates = build_update_candidates([], local, official, diff)
    assert candidates[0]["summary"]
    assert any("定位" in line for line in candidates[0]["summary"])
    assert "共计" in candidates[0]["local_full"]
    assert "控制" in candidates[0]["official_full"]


def test_build_update_candidates_new_with_official_summary() -> None:
    official = [_hero_dict(188, "东方朔", position="辅助")]
    diff = {"added": [{"name": "东方朔", "id": 188}], "modified": [], "removed": []}
    candidates = build_update_candidates([], [], official, diff)
    assert candidates[0]["summary"] == ["官网新增：东方朔（本地未收录，ID 188）"]
    assert candidates[0]["known"] is False
