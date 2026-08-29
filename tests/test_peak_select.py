"""巅峰赛选将 watcher 纯逻辑与面板冒烟测试。"""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from src.business.recognition.peak_select_watcher import (
    PeakSelectWatcher,
    board_signature,
    parse_pool,
)
from src.ui.match.peak_hero_card import PeakHeroCard
from src.ui.match.peak_select_panel import PeakSelectPanel


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_parse_pool_splits_confirmed_and_pending():
    """已确认槽位进候选名单，未确认槽位带候选进入待确认，禁选差集正确。"""
    results = [
        {"name": "荆轲", "resolution": "exact", "raw_name": "荆轲", "candidates": ["荆轲"]},
        {"name": "", "raw_name": "典韦", "candidates": ["典韦", "陆机"], "resolution": "unresolved"},
        {"name": "", "raw_name": "", "candidates": [], "resolution": "unknown"},
    ]

    snapshot = parse_pool(results, 9, ban_names=("陈阿娇", "荆轲"))

    assert snapshot.names == ("荆轲",)
    assert snapshot.pending == ({"slot": 1, "raw_name": "典韦", "candidates": ["典韦", "陆机"]},)
    assert snapshot.stage == "pick"
    assert snapshot.overlap == 1
    assert snapshot.banned == ("陈阿娇",)


def test_parse_pool_applies_manual_resolutions():
    """人工确认仅在确认名属于该槽候选内时生效，防旧牌面确认串台。"""
    results = [
        {"name": "", "raw_name": "卓文君", "candidates": ["卓文君", "君王后"], "resolution": "conflict"},
    ]

    resolved = parse_pool(results, 14, resolutions={0: "卓文君"})
    assert resolved.names == ("卓文君",)
    assert resolved.pending == ()

    invalid = parse_pool(results, 14, resolutions={0: "荆轲"})
    assert invalid.names == ()
    assert invalid.pending[0]["slot"] == 0


def test_parse_pool_ban_stage_keeps_full_board():
    """14 张牌判为禁选阶段，不计算撞车数。"""
    results = [{"name": f"武将{i}", "resolution": "exact"} for i in range(14)]

    snapshot = parse_pool(results, 14)

    assert snapshot.stage == "ban"
    assert snapshot.overlap == 0
    assert len(snapshot.names) == 14


def test_board_signature_ignores_pixel_jitter():
    """签名对 1~2px 检测抖动稳定，对布局变化敏感。"""
    cards = [(100, 200, 238, 326), (400, 200, 238, 326)]
    jittered = [(101, 202, 239, 325), (399, 198, 237, 327)]
    moved = [(100, 600, 238, 326), (400, 200, 238, 326)]

    assert board_signature(cards) == board_signature(jittered)
    assert board_signature(cards) != board_signature(moved)


def _make_panel(
    capture=None,
    hero_manager=None,
    win_rates=None,
    combo_manager=None,
) -> PeakSelectPanel:
    return PeakSelectPanel(
        capture_service=SimpleNamespace(capture=capture),
        ocr_service=None,
        hero_names_provider=lambda: [],
        hero_manager=hero_manager,
        win_rates_provider=(lambda: win_rates) if win_rates is not None else None,
        combo_manager=combo_manager,
    )


def test_panel_renders_pool_snapshot(qapp):
    """池子快照驱动阶段徽章、汇总、候选卡片、待确认与已禁展示。"""
    panel = _make_panel()
    results = [
        {"name": "荆轲", "resolution": "exact"},
        {"name": "", "raw_name": "典韦", "candidates": ["典韦"], "resolution": "unresolved"},
    ]
    panel._on_pool_updated(parse_pool(results, 9, ban_names=("陈阿娇", "荆轲")))

    assert "候选阶段" in panel._stage_badge.text()
    assert "9" in panel._summary_label.text()
    assert "撞车 1" in panel._summary_label.text()
    assert not panel._cards_section.isHidden()
    assert "荆轲" in [card._name_overlay.text() for card in panel._cards]
    assert "典韦" in [button.text() for button in panel._pending_area.findChildren(QPushButton)]
    assert not panel._pending_area.isHidden()
    banned_texts = [widget.text() for widget in panel._banned_area.findChildren(QLabel)]
    assert "陈阿娇" in banned_texts
    assert panel._empty_state.isHidden()


def test_panel_renders_ban_stage(qapp):
    """禁选阶段快照不显示撞车数与已禁差集。"""
    panel = _make_panel()
    results = [{"name": f"武将{i}", "resolution": "exact"} for i in range(14)]

    panel._on_pool_updated(parse_pool(results, 14))

    assert "禁选阶段" in panel._stage_badge.text()
    assert "撞车" not in panel._summary_label.text()
    assert panel._banned_area.isHidden()


def test_panel_cards_render_win_rate_and_sort(qapp):
    """卡片显示巅峰赛单将胜率，排序开关按胜率降序且无胜率沉底。"""
    heroes = {
        "荆轲": SimpleNamespace(id=1, name="荆轲", faction="燕"),
        "傅玄": SimpleNamespace(id=2, name="傅玄", faction="曹魏"),
        "蒙恬": SimpleNamespace(id=3, name="蒙恬", faction="秦"),
    }
    win_rates = {"荆轲": 52.3, "傅玄": 47.7}
    panel = _make_panel(
        hero_manager=SimpleNamespace(get_hero_by_name=heroes.get),
        win_rates=win_rates,
    )
    results = [
        {"name": "傅玄", "resolution": "exact"},
        {"name": "蒙恬", "resolution": "exact"},
        {"name": "荆轲", "resolution": "exact"},
    ]
    panel._on_pool_updated(parse_pool(results, 9))

    assert [card._win_rate_label.text() for card in panel._cards] == [
        "单将胜率：47.7%",
        "单将胜率：暂无数据",
        "单将胜率：52.3%",
    ]

    panel._sort_button.setChecked(True)

    assert [card._name_overlay.text() for card in panel._cards] == ["荆轲", "傅玄", "蒙恬"]
    assert panel._cards[2]._win_rate_label.text() == "单将胜率：暂无数据"


def test_panel_combo_strip_matches_and_badges(qapp):
    """池子命中的实战配队渲染 chip 行，参战武将卡显示最高评级角标。"""
    heroes = {
        "荆轲": SimpleNamespace(id=1, name="荆轲", faction="燕"),
        "君王后": SimpleNamespace(id=3, name="君王后", faction="齐"),
        "蒙恬": SimpleNamespace(id=4, name="蒙恬", faction="秦"),
    }
    combo = SimpleNamespace(
        hero1_id=1,
        hero2_id=3,
        hero1_name="荆轲",
        hero2_name="君王后",
        hero1_seats=[1, 2],
        hero2_seats=[3],
        rating=9,
        note="先手控场",
    )
    panel = _make_panel(
        hero_manager=SimpleNamespace(get_hero_by_name=heroes.get),
        combo_manager=SimpleNamespace(list_combos=lambda: [combo]),
    )
    results = [
        {"name": "荆轲", "resolution": "exact"},
        {"name": "君王后", "resolution": "exact"},
        {"name": "蒙恬", "resolution": "exact"},
    ]
    panel._on_pool_updated(parse_pool(results, 9))

    assert "命中 1" in panel._combo_title.text()
    assert not panel._combo_strip.isHidden()
    card_badges = [card._combo_badge.text() for card in panel._cards]
    assert "实战 ★9" in card_badges
    assert "实战 ★9" not in [card._combo_badge.text() for card in panel._cards if card._name_overlay.text() == "蒙恬"]
    chips = [widget.text() for widget in panel._combo_chips_container.findChildren(QPushButton)]
    assert any("荆轲[1/2] + 君王后[3]" in text for text in chips)


def test_peak_hero_card_states(qapp):
    """卡片三态：待确认/已确认/无武将数据，胜率与角标展示正确。"""
    card = PeakHeroCard()
    hero = SimpleNamespace(id=1, name="荆轲", faction="燕")

    card.set_hero(None, display_name="卓文君", confirmed=False)
    assert card._name_overlay.text() == "卓文君"
    assert card._status_label.text() == "待确认"
    assert card._win_rate_label.text() == "单将胜率：--"

    card.set_hero(hero, confirmed=True)
    assert card._name_overlay.text() == "荆轲"
    assert card._status_label.text() == "已确认"

    card.set_win_rate(48.12)
    assert card._win_rate_label.text() == "单将胜率：48.1%"

    card.set_combo_badge("实战 ★9")
    assert card._combo_badge.text() == "实战 ★9"
    assert not card._combo_badge.isHidden()


def test_panel_start_without_capture_prompts_config(qapp):
    """未连接模拟器时点开始，提示配置且不启动循环。"""
    panel = _make_panel(capture=None)
    requested = []
    panel.request_mumu_config.connect(lambda: requested.append(True))

    panel._toggle_button.click()

    assert requested == [True]
    assert not panel._watcher.is_running()


def test_panel_toggle_starts_and_stops_watcher(qapp):
    """开始/停止按钮切换识别循环状态。"""
    panel = _make_panel(capture=SimpleNamespace(connected=True))

    panel._toggle_button.click()
    assert panel._watcher.is_running()
    assert panel._toggle_button.text() == "停止识别"

    panel._toggle_button.click()
    assert not panel._watcher.is_running()
    assert panel._toggle_button.text() == "开始识别"


def _fake_ocr_task(ocr_results: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        completed=_AlreadySetEvent(),
        result={"outcome": "matched", "ocr_results": ocr_results},
    )


class _AlreadySetEvent(threading.Event):
    def __init__(self) -> None:
        super().__init__()
        self.set()


def _make_watcher(capture_service) -> tuple[PeakSelectWatcher, list, list]:
    watcher = PeakSelectWatcher(
        capture_service,
        None,
        lambda: ["荆轲", "典韦"],
    )
    pools: list = []
    statuses: list[str] = []
    watcher.pool_updated.connect(pools.append)
    watcher.status_changed.connect(statuses.append)
    return watcher, pools, statuses


def test_watcher_file_recognition_publishes_pool(qapp, monkeypatch, tmp_path):
    """导入图片走完整链路：加载 → 检测 → OCR → 池子快照推送。"""
    monkeypatch.setattr(
        "src.business.recognition.peak_select_watcher.load_local_image",
        lambda path: Image.new("RGB", (2560, 1440)),
    )
    fake_cards = [(100 + i * 276, 247, 238, 326) for i in range(9)]
    monkeypatch.setattr(
        "src.business.recognition.peak_select_watcher.detect_selection_cards",
        lambda frame: fake_cards,
    )
    submitted: dict = {}

    def fake_submit(image, hero_names=None, **kwargs):
        submitted.update(kwargs, hero_names=hero_names)
        return _fake_ocr_task(
            [
                {"name": "荆轲", "resolution": "exact"},
                {"name": "", "raw_name": "典韦", "candidates": ["典韦"], "resolution": "unresolved"},
            ]
        )

    capture_service = SimpleNamespace(submit_ocr_task=fake_submit)
    watcher, pools, statuses = _make_watcher(capture_service)

    watcher._do_file_recognition(str(tmp_path / "sample.png"))

    assert submitted["match_template"] is False
    assert len(submitted["rois"]) == 9
    assert len(pools) == 1
    assert pools[0].card_count == 9
    assert pools[0].names == ("荆轲",)
    assert pools[0].pending[0]["raw_name"] == "典韦"
    assert statuses[-1] == "图片识别完成"


def test_watcher_file_recognition_rejects_non_board_image(qapp, monkeypatch, tmp_path):
    """图片中检测不到 2v2 牌面时不提交 OCR，仅提示状态。"""
    monkeypatch.setattr(
        "src.business.recognition.peak_select_watcher.load_local_image",
        lambda path: Image.new("RGB", (2560, 1440)),
    )
    monkeypatch.setattr(
        "src.business.recognition.peak_select_watcher.detect_selection_cards",
        lambda frame: None,
    )

    def fail_submit(*args, **kwargs):
        raise AssertionError("不应提交 OCR")

    watcher, pools, statuses = _make_watcher(SimpleNamespace(submit_ocr_task=fail_submit))

    watcher._do_file_recognition(str(tmp_path / "sample.png"))

    assert pools == []
    assert statuses == ["未在图片中检测到巅峰赛牌面（需 8~14 张卡）"]


def test_panel_import_button_passes_file_to_watcher(qapp, monkeypatch, tmp_path):
    """导入按钮打开文件对话框并把选中路径交给 watcher。"""
    panel = _make_panel(capture=SimpleNamespace(connected=False))
    target = tmp_path / "pool_9.png"
    monkeypatch.setattr(
        "src.ui.match.peak_select_panel.QFileDialog.getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(target), "")),
    )
    forwarded: list[str] = []
    monkeypatch.setattr(panel._watcher, "recognize_image_file", forwarded.append)

    panel._import_button.click()

    assert forwarded == [str(target)]
    assert "正在识别导入图片" in panel._action_bar.status_label.text()

    monkeypatch.setattr(
        "src.ui.match.peak_select_panel.QFileDialog.getOpenFileName",
        staticmethod(lambda *args, **kwargs: ("", "")),
    )
    panel._import_button.click()
    assert forwarded == [str(target)]


def test_panel_pending_candidate_click_confirms(qapp, monkeypatch):
    """待确认槽位的候选按钮点击即触发人工确认。"""
    panel = _make_panel()
    results = [
        {"name": "", "raw_name": "卓文君", "candidates": ["卓文君", "君王后"], "resolution": "conflict"},
    ]
    panel._on_pool_updated(parse_pool(results, 14))

    buttons = panel._pending_area.findChildren(QPushButton)
    assert [button.text() for button in buttons] == ["卓文君", "君王后"]

    confirmed = []
    monkeypatch.setattr(
        panel._watcher,
        "confirm_pending",
        lambda slot, name: confirmed.append((slot, name)),
    )
    buttons[0].click()

    assert confirmed == [(0, "卓文君")]


def test_watcher_confirm_pending_republishes_snapshot(qapp):
    """人工确认后立即重发快照：候选入池，禁选阶段基线同步更新。"""
    watcher, pools, _ = _make_watcher(SimpleNamespace(submit_ocr_task=None))
    results = [
        {"name": "荆轲", "resolution": "exact"},
        {"name": "", "raw_name": "卓文君", "candidates": ["卓文君", "君王后"], "resolution": "conflict"},
    ]

    watcher._publish_pool(results, 14)
    assert pools[0].names == ("荆轲",)
    assert len(pools[0].pending) == 1

    watcher.confirm_pending(1, "卓文君")

    assert len(pools) == 2
    assert pools[1].names == ("荆轲", "卓文君")
    assert pools[1].pending == ()
    assert pools[1].banned == ()
