# -*- coding: utf-8 -*-
"""数据新鲜度芯片（src/ui/app/data_freshness_chip.py）offscreen UI 测试。

fake 服务仿 test_auto_check._FakeService 的 Signal 假对象模式；compute 用
monkeypatch 替身隔离真实数据盘；QMenu 只测 _build_menu 结构（exec 为模态）。
"""

from __future__ import annotations

import types

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from src.business.freshness import (
    GREEN,
    RED,
    SUGGEST_INCREMENTAL,
    SUGGEST_SPECIFIC,
    YELLOW,
    FreshnessItem,
    FreshnessReport,
)
from src.ui.app import data_freshness_chip
from src.ui.app.data_freshness_chip import DataFreshnessChip


class _HeroFetchFake(QObject):
    fetch_completed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.busy = False

    @property
    def is_busy(self) -> bool:
        return self.busy


class _DetailFetchFake(QObject):
    fetch_completed = Signal(bool, str)
    is_busy = False


class _WorkflowFake(QObject):
    guides_changed = Signal()
    synergies_changed = Signal()


class _AnnouncementFake(QObject):
    check_finished = Signal(object)


class _Fakes:
    def __init__(self):
        self.hero = _HeroFetchFake()
        self.guide = _DetailFetchFake()
        self.synergy = _DetailFetchFake()
        self.workflow = _WorkflowFake()
        self.announcement = _AnnouncementFake()


def _report(
    state: str = GREEN,
    headline: str = "数据：2天前",
    suggested: tuple[str, ...] = (),
    warning: str | None = None,
) -> FreshnessReport:
    items = (
        FreshnessItem("heroes", GREEN, "2天前", "武将：最新 2026-10-08（2天前）", 2),
        FreshnessItem("guides", GREEN, "2天前", "攻略：已对齐武将数据", 2),
        FreshnessItem("synergies", GREEN, "2天前", "相性：已对齐武将最新数据", 2),
        FreshnessItem("rankings", GREEN, "榜单5天前", "榜单：最旧 2v2胜率排行.csv（5天前）", 5),
    )
    return FreshnessReport(items, state, headline, warning, suggested)


def _make_chip(monkeypatch, tmp_path, report: FreshnessReport | None, counts: dict):
    """构造接好 fake 服务的芯片；compute 替身返回 report 并计数。"""
    monkeypatch.setattr(
        data_freshness_chip, "compute_freshness_report",
        lambda: (counts.__setitem__("n", counts["n"] + 1), report or _report())[1],
    )
    monkeypatch.setattr(data_freshness_chip, "WATCH_DIR", tmp_path)
    fakes = _Fakes()
    chip = DataFreshnessChip(
        hero_fetch_service=fakes.hero,
        guide_fetch_service=fakes.guide,
        synergy_fetch_service=fakes.synergy,
        ai_workflow=fakes.workflow,
        announcement_service=fakes.announcement,
    )
    return chip, fakes


def _menu_texts(menu) -> list[str]:
    return [action.text() for action in menu.actions()]


# ============================================================
# 渲染
# ============================================================


def test_initial_render_green(qapp, monkeypatch, tmp_path) -> None:
    counts = {"n": 0}
    chip, _ = _make_chip(monkeypatch, tmp_path, _report(), counts)
    assert counts["n"] == 1
    assert chip.text() == "数据：2天前"
    assert "#176b36" in chip.styleSheet()
    assert "#e4f5e8" in chip.styleSheet()
    assert "border-radius: 8px" in chip.styleSheet()


def test_render_red_uses_red_palette(qapp, monkeypatch, tmp_path) -> None:
    chip, _ = _make_chip(monkeypatch, tmp_path, _report(RED, "数据：落后2次官方更新"), {"n": 0})
    assert "#a12622" in chip.styleSheet()
    assert "#fde8e8" in chip.styleSheet()


# ============================================================
# 菜单结构
# ============================================================


def test_menu_has_three_sections(qapp, monkeypatch, tmp_path) -> None:
    chip, _ = _make_chip(monkeypatch, tmp_path, _report(), {"n": 0})
    texts = _menu_texts(chip._build_menu(chip._report))
    assert "内容年龄" in texts
    assert "数据源周期" in texts
    assert "武将：最新 2026-10-08（2天前）" in texts
    assert "榜单：最旧 2v2胜率排行.csv（5天前）" in texts
    for row in data_freshness_chip.SOURCE_CYCLE_ROWS:
        assert row in texts


def test_menu_shows_timeline_warning_row(qapp, monkeypatch, tmp_path) -> None:
    chip, _ = _make_chip(
        monkeypatch, tmp_path,
        _report(warning="时间轴超过14天未更新，武将新鲜度按纯年龄估算"), {"n": 0},
    )
    texts = _menu_texts(chip._build_menu(chip._report))
    assert "⚠ 时间轴超过14天未更新，武将新鲜度按纯年龄估算" in texts


def test_menu_suggestion_action_incremental(qapp, monkeypatch, tmp_path) -> None:
    chip, _ = _make_chip(
        monkeypatch, tmp_path, _report(suggested=(SUGGEST_INCREMENTAL,)), {"n": 0},
    )
    menu = chip._build_menu(chip._report)
    texts = _menu_texts(menu)
    assert "建议操作" in texts
    action = next(a for a in menu.actions() if a.text() == "增量获取武将数据（拉取新增武将）")
    assert action.isEnabled()


def test_menu_shows_both_actions_for_mixed_batch(qapp, monkeypatch, tmp_path) -> None:
    """混合批次（缺失新武将 + 过期旧武将）：增量与指定两个动作并给。"""
    chip, _ = _make_chip(
        monkeypatch, tmp_path,
        _report(suggested=(SUGGEST_INCREMENTAL, SUGGEST_SPECIFIC)), {"n": 0},
    )
    menu = chip._build_menu(chip._report)
    texts = _menu_texts(menu)
    assert "增量获取武将数据（拉取新增武将）" in texts
    action = next(a for a in menu.actions()
                  if a.text() == "指定获取武将数据（替换模式，刷新过期武将）…")
    assert action.isEnabled()


def test_menu_advice_rows_for_stale_non_hero_items(qapp, monkeypatch, tmp_path) -> None:
    items = (
        FreshnessItem("heroes", GREEN, "2天前", "武将：最新 2026-10-08（2天前）", 2),
        FreshnessItem("guides", YELLOW, "攻略落后1天", "攻略：落后武将数据1天", 1),
        FreshnessItem("synergies", GREEN, "2天前", "相性：已对齐", 2),
        FreshnessItem("rankings", RED, "榜单21天前", "榜单：最旧 x.csv（21天前）", 21),
    )
    report = FreshnessReport(items, RED, "数据：榜单21天前", None, ())
    chip, _ = _make_chip(monkeypatch, tmp_path, report, {"n": 0})
    texts = _menu_texts(chip._build_menu(chip._report))
    assert "攻略落后或未生成：建议在资料库重新生成攻略" in texts
    assert "榜单过期或缺失：建议通过 OCR 导入最新榜单" in texts
    assert "相性落后或未生成：建议在资料库重新生成相性" not in texts


def test_menu_omits_suggestion_section_when_clean(qapp, monkeypatch, tmp_path) -> None:
    chip, _ = _make_chip(monkeypatch, tmp_path, _report(), {"n": 0})
    assert "建议操作" not in _menu_texts(chip._build_menu(chip._report))


# ============================================================
# 建议动作与忙碌守卫
# ============================================================


def test_busy_guard_toasts_and_blocks_signal(qapp, monkeypatch, tmp_path) -> None:
    toasts: list[tuple] = []
    monkeypatch.setattr(
        data_freshness_chip, "show_toast",
        lambda parent, message, tone=None, duration=None: toasts.append((message, tone)),
    )
    chip, fakes = _make_chip(monkeypatch, tmp_path, _report(), {"n": 0})
    emitted: list[str] = []
    chip.fetch_incremental_requested.connect(lambda: emitted.append("inc"))
    chip.fetch_specific_requested.connect(lambda: emitted.append("spec"))

    fakes.hero.busy = True
    chip._request_incremental()
    chip._request_specific()
    assert emitted == []
    assert toasts == [("任务进行中", data_freshness_chip.TONE_WARNING)] * 2

    fakes.hero.busy = False
    chip._request_incremental()
    chip._request_specific()
    assert emitted == ["inc", "spec"]
    assert len(toasts) == 2


# ============================================================
# 信号驱动刷新
# ============================================================


def test_service_signals_trigger_refresh(qapp, monkeypatch, tmp_path) -> None:
    counts = {"n": 0}
    chip, fakes = _make_chip(monkeypatch, tmp_path, _report(), counts)
    assert counts["n"] == 1
    fakes.hero.fetch_completed.emit(True)
    fakes.guide.fetch_completed.emit(True, "done")
    fakes.synergy.fetch_completed.emit(False, "err")
    fakes.workflow.guides_changed.emit()
    fakes.workflow.synergies_changed.emit()
    fakes.announcement.check_finished.emit(object())
    assert counts["n"] == 7


# ============================================================
# 目录监听（mtime 快照过滤 + 去抖）
# ============================================================


def test_watched_file_change_refreshes_after_debounce(qapp, monkeypatch, tmp_path) -> None:
    counts = {"n": 0}
    _make_chip(monkeypatch, tmp_path, _report(), counts)
    assert counts["n"] == 1
    (tmp_path / "heroes.json").write_text("[]", encoding="utf-8")
    QTest.qWait(900)
    assert counts["n"] == 2


def test_unrelated_file_change_does_not_refresh(qapp, monkeypatch, tmp_path) -> None:
    counts = {"n": 0}
    _make_chip(monkeypatch, tmp_path, _report(), counts)
    (tmp_path / "cards.json").write_text("[]", encoding="utf-8")
    QTest.qWait(900)
    assert counts["n"] == 1


# ============================================================
# 键盘与鼠标入口
# ============================================================


def test_mouse_and_key_open_menu(qapp, monkeypatch, tmp_path) -> None:
    chip, _ = _make_chip(monkeypatch, tmp_path, _report(), {"n": 0})
    opened: list[int] = []
    monkeypatch.setattr(chip, "_open_menu", lambda: opened.append(1))
    mouse_event = types.SimpleNamespace(accept=lambda: None)
    chip.mousePressEvent(mouse_event)
    event = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier,
    )
    chip.keyPressEvent(event)
    event_down = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier,
    )
    chip.keyPressEvent(event_down)
    assert opened == [1, 1, 1]
