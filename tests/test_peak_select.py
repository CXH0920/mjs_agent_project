"""巅峰赛选将 watcher 纯逻辑与面板冒烟测试。"""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from src.business.analysis.peak_ban_advice import PeakBanAdvice
from src.business.recognition.peak_select_watcher import (
    PeakSelectWatcher,
    board_signature,
    carry_over_resolutions,
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


def test_carry_over_resolutions_keeps_migrates_and_drops():
    """确认跟随内容：槽位未变原地保留，重排迁移到内容匹配槽位，内容消失自然失效。"""
    ocr = [
        {"resolution": "unresolved", "raw_name": "荀歇", "candidates": ["荀勖", "荀彧"]},
        {"resolution": "exact", "name": "袁术", "candidates": ["袁术"]},  # 自动确认槽不接入
        {"resolution": "unresolved", "raw_name": "黄忠", "candidates": ["黄忠"]},
        {"resolution": "unresolved", "raw_name": "凌统", "candidates": ["凌统"]},
    ]

    assert carry_over_resolutions({0: "荀勖", 2: "黄忠"}, ocr) == {0: "荀勖", 2: "黄忠"}

    ocr_moved = [
        {"resolution": "unresolved", "raw_name": "荀或", "candidates": ["荀彧", "荀灌"]},
        {"resolution": "exact", "name": "袁术", "candidates": ["袁术"]},
        {"resolution": "unresolved", "raw_name": "凌统", "candidates": ["凌统"]},
        {"resolution": "unresolved", "raw_name": "黄忠", "candidates": ["黄忠", "黄盖"]},
    ]

    # 荀勖所在卡被选走（候选集消失）确认失效；黄忠重排迁移到新槽位
    assert carry_over_resolutions({0: "荀勖", 2: "黄忠"}, ocr_moved) == {3: "黄忠"}


def test_carry_over_resolutions_drops_ambiguous_slot():
    """候选集同时命中两个已确认名的歧义槽位保守丢弃，不猜测归属。"""
    ocr = [{"resolution": "unresolved", "candidates": ["黄忠", "凌统"]}]

    assert carry_over_resolutions({1: "黄忠", 2: "凌统"}, ocr) == {}


def test_board_signature_ignores_animation_drift():
    """签名吸收候选阶段卡面浮动动画的实测漂移（y±3、剪影 h±5），真实位移仍翻转。

    漂移数据取自 2026-09-06 日志实测：卡2 三拍 bbox (904,297,71,124)/
    (904,294,71,122)/(904,296,71,124)，旧步长 (4/8) 下 h 跨桶逐拍翻转。
    """
    cards = [(200, 200, 238, 326), (520, 200, 238, 326)]
    drifted = [(197, 203, 237, 324), (523, 197, 239, 328)]
    moved = [(200, 600, 238, 326), (520, 200, 238, 326)]
    measured = [(904, 297, 71, 124), (904, 294, 71, 122), (904, 296, 71, 124)]

    assert board_signature(cards) == board_signature(drifted)
    assert len({board_signature([card]) for card in measured}) == 1
    assert board_signature(cards) != board_signature(moved)


def test_board_signature_flips_on_card_count_change():
    """卡数变化（换人/禁选）必然翻转签名，与量化步长无关。"""
    ten_cards = [(904, 297, 71, 124)] * 10
    nine_cards = [(904, 297, 71, 124)] * 9

    assert board_signature(ten_cards) != board_signature(nine_cards)


def _make_panel(
    capture=None,
    hero_manager=None,
    win_rates=None,
    pick_ranks=None,
    combo_manager=None,
    ocr_service=None,
    **service_attrs,
) -> PeakSelectPanel:
    return PeakSelectPanel(
        capture_service=SimpleNamespace(capture=capture, **service_attrs),
        ocr_service=ocr_service or _FakeOcrService(),
        hero_names_provider=lambda: [],
        hero_manager=hero_manager,
        win_rates_provider=(lambda: win_rates) if win_rates is not None else None,
        pick_ranks_provider=(lambda: pick_ranks) if pick_ranks is not None else None,
        combo_manager=combo_manager,
    )


class _FakeOcrService:
    """覆盖 watcher 协调标准轮询任务（挂起/恢复/清冷却/作废在途）所需的最小接口。"""

    def __init__(self, active_states: dict[str, bool] | None = None) -> None:
        self._tasks = {
            "hero_selection": SimpleNamespace(active=True, cooldown_until=None),
            "match_guide": SimpleNamespace(active=False, cooldown_until=None),
        }
        for name, active in (active_states or {}).items():
            self._tasks[name].active = active
        self.cleared_cooldowns: list[str] = []
        self.invalidated_polls = 0

    def get_task_state(self, name):
        return self._tasks[name]

    def activate_task(self, name) -> None:
        self._tasks[name].active = True

    def deactivate_task(self, name) -> None:
        self._tasks[name].active = False

    def clear_task_cooldown(self, name) -> None:
        self.cleared_cooldowns.append(name)

    def invalidate_inflight_poll(self) -> None:
        self.invalidated_polls += 1


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
        "胜率：47.7%",
        "胜率：暂无数据",
        "胜率：52.3%",
    ]

    panel._sort_button.setChecked(True)

    assert [card._name_overlay.text() for card in panel._cards] == ["荆轲", "傅玄", "蒙恬"]
    assert panel._cards[2]._win_rate_label.text() == "胜率：暂无数据"


def test_panel_cards_render_ban_advice(qapp):
    """强势象限武将卡显示禁选建议徽章，弱势/缺数据武将无标签。"""
    heroes = {
        "王濬": SimpleNamespace(id=1, name="王濬", faction="西晋"),
        "荆轲": SimpleNamespace(id=2, name="荆轲", faction="燕"),
        "甘宁": SimpleNamespace(id=3, name="甘宁", faction="吴"),
    }
    panel = _make_panel(
        hero_manager=SimpleNamespace(get_hero_by_name=heroes.get),
        win_rates={"王濬": 71.97, "甘宁": 32.1},
        pick_ranks={"王濬": 165, "荆轲": 1, "甘宁": 1},
    )
    results = [
        {"name": "王濬", "resolution": "exact"},
        {"name": "荆轲", "resolution": "exact"},
        {"name": "甘宁", "resolution": "exact"},
    ]
    panel._on_pool_updated(parse_pool(results, 9))

    advice_by_name = {
        card._name_overlay.text(): card._ban_advice_label for card in panel._cards
    }
    assert advice_by_name["王濬"].text() == "Ban 位首选"
    assert "被 Ban 压制的强势冷门" in advice_by_name["王濬"].toolTip()
    assert "BPI 1164" in advice_by_name["王濬"].toolTip()
    assert not advice_by_name["王濬"].isHidden()
    assert advice_by_name["荆轲"].isHidden()  # 胜率缺失
    assert advice_by_name["甘宁"].isHidden()  # 虚热陷阱不打标签


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
    """卡片三态：待确认显示徽章，确认态不显示徽章；胜率与角标展示正确。"""
    card = PeakHeroCard()
    hero = SimpleNamespace(id=1, name="荆轲", faction="燕")

    card.set_hero(None, display_name="卓文君", confirmed=False)
    assert card._name_overlay.text() == "卓文君"
    assert card._status_label.text() == "待确认"
    assert not card._status_label.isHidden()
    assert card._win_rate_label.text() == "胜率：--"

    card.set_hero(hero, confirmed=True)
    assert card._name_overlay.text() == "荆轲"
    assert card._status_label.text() == "已确认"
    assert card._status_label.isHidden()

    card.set_win_rate(48.12)
    assert card._win_rate_label.text() == "胜率：48.1%"

    card.set_ban_advice(
        PeakBanAdvice(key="ban_first", label="Ban 位首选", detail="d", weight=1000, bpi=1164)
    )
    assert card._ban_advice_label.text() == "Ban 位首选"
    assert not card._ban_advice_label.isHidden()
    card.set_ban_advice(None)
    assert card._ban_advice_label.isHidden()

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
    """开始/停止按钮切换识别循环状态；启动即挂起标准轮询任务并清除冷却污染。"""
    ocr_service = _FakeOcrService()
    panel = _make_panel(capture=SimpleNamespace(connected=True), ocr_service=ocr_service)

    panel._toggle_button.click()
    assert panel._watcher.is_running()
    assert panel._toggle_button.text() == "停止识别"
    assert ocr_service.get_task_state("hero_selection").active is False  # start 即挂起，不等牌面
    assert ocr_service.cleared_cooldowns == ["hero_selection", "match_guide"]

    panel._toggle_button.click()
    assert not panel._watcher.is_running()
    assert panel._toggle_button.text() == "开始识别"
    assert ocr_service.get_task_state("hero_selection").active is True  # 恢复原状态


def test_panel_save_screenshot_without_capture_prompts_config(qapp):
    """未连接模拟器时保存截图引导配置，不发捕获请求。"""
    panel = _make_panel(capture=None, do_capture=lambda **kwargs: pytest.fail("不应触发截图"))
    requested = []
    panel.request_mumu_config.connect(lambda: requested.append(True))

    panel._save_action.trigger()

    assert requested == [True]
    assert "未连接模拟器" in panel._action_bar.status_label.text()


def test_panel_save_screenshot_captures_without_ocr(qapp):
    """保存截图走 do_capture(perform_ocr=False)，完成后回显保存路径。"""
    calls = []
    panel = _make_panel(capture=object(), do_capture=lambda **kwargs: calls.append(kwargs))

    panel._save_action.trigger()
    assert calls == [{"perform_ocr": False}]
    assert "正在保存截图" in panel._action_bar.status_label.text()

    # 在途锁未释放前忽略重复触发
    panel._save_action.trigger()
    assert len(calls) == 1

    panel._on_capture_result({"save_path": "screenshots/screenshot_1.png"})
    assert "screenshot_1.png" in panel._action_bar.status_label.text()

    # 空闲状态下其他面板的捕获回调不改动动作栏状态
    panel._on_capture_result({"save_path": "screenshots/foreign.png"})
    assert "screenshot_1.png" in panel._action_bar.status_label.text()


def test_panel_save_screenshot_failure_shows_warning(qapp):
    """截图失败在动作栏给出警告状态。"""
    panel = _make_panel(capture=object(), do_capture=lambda **kwargs: None)

    panel._save_action.trigger()
    panel._on_capture_failed("ADB 未连接")

    assert "截图保存失败" in panel._action_bar.status_label.text()


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

    panel._import_action.trigger()

    assert forwarded == [str(target)]
    assert "正在识别导入图片" in panel._action_bar.status_label.text()

    monkeypatch.setattr(
        "src.ui.match.peak_select_panel.QFileDialog.getOpenFileName",
        staticmethod(lambda *args, **kwargs: ("", "")),
    )
    panel._import_action.trigger()
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


def test_confirm_pending_waits_for_ongoing_publish(qapp, monkeypatch):
    """回归：GUI 确认与识别线程发布并发时互斥，确认不得插入发布中途改写共享状态。"""
    watcher, pools, _ = _make_watcher(SimpleNamespace(submit_ocr_task=None))
    real_parse = parse_pool
    parsing = threading.Event()
    release = threading.Event()

    def slow_parse(ocr_results, card_count, ban_names=(), resolutions=None):
        parsing.set()
        release.wait(1)
        return real_parse(ocr_results, card_count, ban_names, resolutions)

    monkeypatch.setattr(
        "src.business.recognition.peak_select_watcher.parse_pool", slow_parse
    )

    published = threading.Event()

    def publish():
        watcher._publish_pool([], 9)
        published.set()

    worker = threading.Thread(target=publish, daemon=True)
    worker.start()
    assert parsing.wait(1)

    confirmed = threading.Event()

    def confirm():
        watcher.confirm_pending(0, "荆轲")
        confirmed.set()

    confirmer = threading.Thread(target=confirm, daemon=True)
    confirmer.start()
    time.sleep(0.05)  # 给确认线程抢锁机会：必须被发布持有的 _state_lock 挡住
    assert confirmer.is_alive() and not confirmed.is_set()

    release.set()
    assert published.wait(1)
    assert confirmed.wait(1)
    worker.join(1)
    confirmer.join(1)

    assert watcher._resolutions == {0: "荆轲"}
    assert watcher._last_board == ([], 9)


def test_pool_updated_emitted_outside_state_lock(qapp):
    """快照信号在 _state_lock 外发射：面板槽内同步回调 confirm_pending 不会死锁。"""
    watcher, pools, _ = _make_watcher(SimpleNamespace(submit_ocr_task=None))
    lock_states = []

    watcher.pool_updated.connect(
        lambda _snapshot: lock_states.append(watcher._state_lock.locked())
    )
    watcher._publish_pool([{"name": "荆轲", "resolution": "exact"}], 9)

    assert lock_states == [False]


def test_panel_warmup_hint_restored_after_ready(qapp):
    """OCR 预热提示在预热结束后撤下并恢复页面默认提示，导入恢复可用。"""
    capture_service = SimpleNamespace(
        capture=None,
        ocr_warmup_state="warming",
        ocr_warmup_state_changed=None,
    )
    panel = PeakSelectPanel(
        capture_service=capture_service,
        ocr_service=None,
        hero_names_provider=lambda: [],
    )

    assert "OCR 模型预热中" in panel._action_bar.status_label.text()
    assert not panel._import_action.isEnabled()

    capture_service.ocr_warmup_state = "ready"
    panel._update_import_availability("ready")

    assert panel._import_action.isEnabled()
    assert "OCR 模型预热中" not in panel._action_bar.status_label.text()
    assert "开始后自动识别巅峰赛禁选结果" in panel._action_bar.status_label.text()


def test_panel_without_warmup_keeps_default_hint(qapp):
    """预热已完成或未启用时，页面保持默认提示，不误显预热文案。"""
    panel = _make_panel()

    assert "OCR 模型预热中" not in panel._action_bar.status_label.text()


class _NeverSetEvent(threading.Event):
    def wait(self, timeout=None):
        return False


def test_watcher_file_recognition_timeout_keeps_live_signature(qapp, monkeypatch, tmp_path):
    """图片导入 OCR 超时不清实时循环签名，下一拍不因签名丢失重复识别。"""
    monkeypatch.setattr(
        "src.business.recognition.peak_select_watcher.load_local_image",
        lambda path: Image.new("RGB", (2560, 1440)),
    )
    monkeypatch.setattr(
        "src.business.recognition.peak_select_watcher.detect_selection_cards",
        lambda frame: [(100 + i * 276, 247, 238, 326) for i in range(9)],
    )
    monkeypatch.setattr(
        "src.business.recognition.peak_select_watcher.OCR_WAIT_TIMEOUT_SECONDS",
        0.01,
    )

    def timeout_submit(image, **kwargs):
        return SimpleNamespace(completed=_NeverSetEvent(), result=None)

    watcher, pools, statuses = _make_watcher(SimpleNamespace(submit_ocr_task=timeout_submit))
    watcher._signature = ((10, 20, 30, 40),)

    watcher._do_file_recognition(str(tmp_path / "sample.png"))

    assert watcher._signature == ((10, 20, 30, 40),)
    assert pools == []
    assert statuses[-1] == "图片识别未完成，请重试"


def test_watcher_live_loop_timeout_resets_signature(qapp, monkeypatch):
    """实时循环 OCR 超时后清签名，下一拍强制重试新牌面。"""
    monkeypatch.setattr(
        "src.business.recognition.peak_select_watcher.detect_selection_cards",
        lambda frame: [(100 + i * 276, 247, 238, 326) for i in range(9)],
    )
    monkeypatch.setattr(
        "src.business.recognition.peak_select_watcher.OCR_WAIT_TIMEOUT_SECONDS",
        0.01,
    )
    capture_service = SimpleNamespace(
        capture=SimpleNamespace(connected=True),
        capture_for_poll=lambda _capture: (True, Image.new("RGB", (2560, 1440)), ""),
        submit_ocr_task=lambda image, **kwargs: SimpleNamespace(
            completed=_NeverSetEvent(), result=None
        ),
    )
    ocr_service = SimpleNamespace(
        get_task_state=lambda name: SimpleNamespace(active=False),
        deactivate_task=lambda name: None,
    )
    watcher, pools, statuses = _make_watcher(capture_service)
    watcher._ocr_service = ocr_service
    watcher._signature = ((1, 2, 3, 4),)
    # 生产中 _do_work 由 _on_tick 持锁后调用，此处同样先持锁
    assert watcher._thread_lock.acquire(blocking=False)
    watcher._do_work()

    assert watcher._signature is None
    assert not watcher._thread_lock.locked()
    assert pools == []
    assert statuses[-1] == "识别超时，下一拍重试"


def test_watcher_start_suspends_standard_tasks_immediately(qapp):
    """回归：挂起在 start 即生效（不等牌面检测），清除冷却污染并作废在途轮询。"""
    ocr_service = _FakeOcrService()
    watcher, _, _ = _make_watcher(SimpleNamespace(submit_ocr_task=None))
    watcher._ocr_service = ocr_service

    watcher.start()

    assert ocr_service.get_task_state("hero_selection").active is False
    assert ocr_service.get_task_state("match_guide").active is False
    assert ocr_service.cleared_cooldowns == ["hero_selection", "match_guide"]
    assert ocr_service.invalidated_polls == 1
    watcher.stop()


def test_watcher_board_absent_restores_match_guide_only(qapp):
    """自动退出仅恢复 match_guide 原状态；hero_selection 整个会话保持挂起，停止才全量恢复。"""
    ocr_service = _FakeOcrService(active_states={"match_guide": True})
    watcher, _, statuses = _make_watcher(SimpleNamespace(submit_ocr_task=None))
    watcher._ocr_service = ocr_service
    exited: list[bool] = []
    watcher.board_exited.connect(lambda: exited.append(True))

    watcher.start()
    assert ocr_service.get_task_state("hero_selection").active is False
    assert ocr_service.get_task_state("match_guide").active is False

    watcher._handle_board_absent()  # 第一拍缺席：尚未退出
    assert exited == []

    watcher._handle_board_absent()
    assert exited == [True]
    assert ocr_service.get_task_state("hero_selection").active is False  # 方案一：保持挂起
    assert ocr_service.get_task_state("match_guide").active is True      # 恢复原状态
    assert statuses[-1] == "未检测到巅峰赛选将页牌面"

    watcher.stop()
    assert ocr_service.get_task_state("hero_selection").active is True
    assert ocr_service.get_task_state("match_guide").active is True


def test_watcher_live_loop_resuspends_on_board_reappear(qapp, monkeypatch):
    """回归：缺席恢复后牌面重现时重新挂起标准任务（_do_work 调用点重武装）。"""
    monkeypatch.setattr(
        "src.business.recognition.peak_select_watcher.detect_selection_cards",
        lambda frame: [(100 + i * 276, 247, 238, 326) for i in range(9)],
    )
    capture_service = SimpleNamespace(
        capture=SimpleNamespace(connected=True),
        capture_for_poll=lambda _capture: (True, Image.new("RGB", (2560, 1440)), ""),
        submit_ocr_task=lambda image, **kwargs: _fake_ocr_task(
            [{"name": "荆轲", "resolution": "exact"}]
        ),
    )
    ocr_service = _FakeOcrService()
    watcher, pools, _ = _make_watcher(capture_service)
    watcher._ocr_service = ocr_service

    watcher.start()
    # 模拟上一局退出后主窗口衔接激活了 match_guide（方案一下 hero_selection 保持挂起）
    ocr_service.activate_task("match_guide")

    assert watcher._thread_lock.acquire(blocking=False)
    watcher._do_work()

    assert ocr_service.get_task_state("hero_selection").active is False
    assert ocr_service.get_task_state("match_guide").active is False  # 牌面重现 → 重新挂起
    assert len(pools) == 1


def test_watcher_carries_resolution_across_signature_flip(qapp, monkeypatch):
    """回归：浮动动画致签名假性翻转（布局跨桶位移）时人工确认按内容沿用不丢。"""
    detect_results = [
        [(100, 247, 238, 326)],   # 第一拍
        [(108, 249, 239, 330)],   # 第二拍：位置跨桶位移（动画/重排），OCR 内容不变
        [(116, 247, 238, 326)],   # 第三拍：内容真变（候选集换人）
    ]
    monkeypatch.setattr(
        "src.business.recognition.peak_select_watcher.detect_selection_cards",
        lambda frame: detect_results.pop(0),
    )
    ocr_results = [
        [{"resolution": "unresolved", "raw_name": "荀歇", "candidates": ["荀勖", "荀彧"]}],
        [{"resolution": "unresolved", "raw_name": "荀歇", "candidates": ["荀勖", "荀彧"]}],
        [{"resolution": "unresolved", "raw_name": "荀或", "candidates": ["荀彧", "荀灌"]}],
    ]
    capture_service = SimpleNamespace(
        capture=SimpleNamespace(connected=True),
        capture_for_poll=lambda _capture: (True, Image.new("RGB", (2560, 1440)), ""),
        submit_ocr_task=lambda image, **kwargs: _fake_ocr_task(ocr_results.pop(0)),
    )
    watcher, pools, _ = _make_watcher(capture_service)
    watcher._ocr_service = _FakeOcrService()

    assert watcher._thread_lock.acquire(blocking=False)
    watcher._do_work()
    watcher.confirm_pending(0, "荀勖")
    assert watcher._resolutions == {0: "荀勖"}

    # 第二拍签名翻转但内容未变：确认沿用，不再回到待确认
    # （pools[1] 是 confirm_pending 的重发快照，pools[2] 才是第二拍产物）
    watcher._thread_lock.acquire(blocking=False)
    watcher._do_work()
    assert watcher._resolutions == {0: "荀勖"}
    assert pools[2].names == ("荀勖",)
    assert pools[2].pending == ()

    # 第三拍内容真变（候选集里没有荀勖）：确认失效
    watcher._thread_lock.acquire(blocking=False)
    watcher._do_work()
    assert watcher._resolutions == {}
    assert pools[-1].pending[0]["candidates"] == ["荀彧", "荀灌"]


def test_watcher_manual_stop_does_not_emit_board_exited(qapp):
    """手动停止不发 board_exited：用户可能要切标准 2v2，match_guide 不被激活。"""
    ocr_service = _FakeOcrService()
    watcher, _, _ = _make_watcher(SimpleNamespace(submit_ocr_task=None))
    watcher._ocr_service = ocr_service
    exited: list[bool] = []
    watcher.board_exited.connect(lambda: exited.append(True))

    watcher.start()
    watcher.stop()

    assert exited == []


def test_panel_forwards_board_exited_signal(qapp):
    """面板把 watcher 的 board_exited 透传给主窗口。"""
    panel = _make_panel()
    forwarded: list[bool] = []
    panel.board_exited.connect(lambda: forwarded.append(True))

    panel._watcher.board_exited.emit()

    assert forwarded == [True]
