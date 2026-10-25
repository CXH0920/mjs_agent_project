"""首启向导单元测试：状态文件容错、分辨率判定、frozen 文案映射（spec_onboarding §八）。"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.config.onboarding_state import (
    ONBOARDING_VERSION,
    mark_skipped,
    mark_step,
    reset,
    should_show_state,
)

# ---------------------------------------------------------------------------
# onboarding_state：缺失 / 损坏 / 版本不符 / 跳过（范式同 test_doctor 的 tmp_path+monkeypatch）
# ---------------------------------------------------------------------------

def test_should_show_when_file_missing(tmp_path):
    assert should_show_state(tmp_path / ".onboarding_state.json") is True


def test_should_show_when_file_corrupted(tmp_path):
    state_file = tmp_path / ".onboarding_state.json"
    state_file.write_text("{ 截断", encoding="utf-8")

    assert should_show_state(state_file) is True


def test_should_show_when_version_mismatched(tmp_path):
    state_file = tmp_path / ".onboarding_state.json"
    state_file.write_text(json.dumps({"onboarding_version": "0", "steps": {}}),
                          encoding="utf-8")

    assert should_show_state(state_file) is True


def test_should_not_show_when_version_matches(tmp_path):
    state_file = tmp_path / ".onboarding_state.json"
    state_file.write_text(json.dumps(
        {"onboarding_version": ONBOARDING_VERSION, "steps": {}, "skipped": True}),
        encoding="utf-8")

    assert should_show_state(state_file) is False


def test_mark_step_records_and_preserves_other_steps(tmp_path):
    state_file = tmp_path / ".onboarding_state.json"
    mark_step("env", state_file)
    mark_step("device", state_file)

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["onboarding_version"] == ONBOARDING_VERSION
    assert set(data["steps"]) == {"env", "device"}
    assert data["skipped"] is False
    assert should_show_state(state_file) is False


def test_mark_step_rebuilds_stale_version_file(tmp_path):
    state_file = tmp_path / ".onboarding_state.json"
    state_file.write_text(json.dumps(
        {"onboarding_version": "0", "steps": {"env": "2020-01-01T00:00:00"}}),
        encoding="utf-8")

    mark_step("recognize", state_file)

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["onboarding_version"] == ONBOARDING_VERSION
    assert "recognize" in data["steps"]  # 旧版本步骤不继承，重建为新版本状态


def test_mark_skipped_suppresses_auto_popup(tmp_path):
    state_file = tmp_path / ".onboarding_state.json"
    mark_step("env", state_file)
    mark_skipped(state_file)

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["skipped"] is True
    assert set(data["steps"]) == {"env"}  # 跳过不清空已完成记录
    assert should_show_state(state_file) is False


def test_reset_brings_wizard_back(tmp_path):
    state_file = tmp_path / ".onboarding_state.json"
    mark_skipped(state_file)
    assert should_show_state(state_file) is False

    reset(state_file)
    reset(state_file)  # 重复 reset（文件已删）不抛错

    assert should_show_state(state_file) is True


def test_mark_step_rejects_unknown_step(tmp_path):
    """steps 键集是 UI 与测试的契约，未知键写进文件会成为无人消费的垃圾数据。"""
    with pytest.raises(ValueError):
        mark_step("unknown_step", tmp_path / ".onboarding_state.json")


# ---------------------------------------------------------------------------
# layout_resolution.compare_resolution：五类判定（spec_onboarding §3.4）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("actual,expected_status", [
    ((2560, 1440), "pass"),    # 与参考完全一致
    ((1920, 1080), "warn"),    # 等比缩放，识别可用但名称框可能偏移
    ((1280, 720), "warn"),     # 等比下限之上（0.5× 参考宽 = 1280）
    ((1080, 1920), "fail"),    # 竖屏
    ((2560, 1080), "fail"),    # 非等比横屏
    ((1000, 563), "fail"),     # 宽 < 参考宽 × 0.5，文字过糊
])
def test_compare_resolution_status(actual, expected_status):
    from src.business.emulator.layout_resolution import compare_resolution

    result = compare_resolution(actual, (2560, 1440))
    assert result["status"] == expected_status
    assert result["detail"]
    if expected_status != "pass":  # PASS 态无动作提示
        assert result["hint"]


# ---------------------------------------------------------------------------
# T10：_load_data 失败标记（向导段 1 数据三态的"加载失败"来源）
# ---------------------------------------------------------------------------

def test_load_data_sets_and_clears_error_marker(monkeypatch):
    """load_all 抛异常时 _dialogs._data_error 留痕并弹告警；重载成功后清空标记。"""
    from PySide6.QtWidgets import QApplication
    from src.data.guide_manager import GuideManager
    from src.data.hero_manager import HeroManager
    from src.data.issues import LoadReport
    from src.data.models import Hero
    from src.data.synergy_manager import SynergyManager
    from src.ui.app.main_window import MainWindow

    # 本测试不依赖 conftest 的 session fixture，QApplication 需自建
    # （裸 instance() 在单跑时返回 None，MainWindow 构造会直接 abort）
    if QApplication.instance() is None:
        QApplication([])
    manager = HeroManager()
    manager._items = {1: Hero(id=1, name="测试武将", faction="魏")}
    window = MainWindow(manager, SynergyManager(), GuideManager())
    try:
        shown = []
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *args, **kwargs: shown.append(" ".join(str(a) for a in args[1:3])))

        def boom():
            raise RuntimeError("heroes.json 缺失")

        monkeypatch.setattr(window._data, "load_all", boom)
        window._load_data()
        assert window._dialogs._data_error == "heroes.json 缺失"
        assert any("数据加载失败" in text and "heroes.json 缺失" in text for text in shown)

        monkeypatch.setattr(window._data, "load_all", lambda: LoadReport())
        window._load_data()
        assert window._dialogs._data_error is None
    finally:
        window.close()
        window.deleteLater()


# ---------------------------------------------------------------------------
# 向导对话框（T4/T5/T6）：offscreen + fake 服务注入，不触真实 ADB / doctor 线程
# ---------------------------------------------------------------------------

from types import SimpleNamespace

import src.ui.app.onboarding_dialog as onboarding_dialog
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget
from src.scripts.doctor import Finding
from src.ui.app.onboarding_dialog import OnboardingDialog


class _FakeOcrCoordinator:
    def __init__(self) -> None:
        self.reset_calls = 0

    def reset_warmup(self) -> None:
        self.reset_calls += 1


class _FakeCaptureService(QObject):
    capture_completed = Signal(dict)
    capture_failed = Signal(str)
    connection_changed = Signal(str, str)
    ocr_warmup_state_changed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self._state = "ready"
        self.ocr_coordinator = _FakeOcrCoordinator()
        self.do_capture_calls: list[dict] = []
        self.warmup_calls: list[list[str]] = []

    @property
    def ocr_warmup_state(self) -> str:
        return self._state

    @property
    def _ocr_coordinator(self) -> _FakeOcrCoordinator:
        return self.ocr_coordinator

    def update_config(self, config: dict) -> None:
        self.last_config = dict(config)

    def do_capture(self, **kwargs) -> None:
        self.do_capture_calls.append(kwargs)

    def warmup_ocr_model(self, names) -> None:
        self.warmup_calls.append(list(names))


class _FakeCoordinator(QObject):
    adb_detected = Signal(bool, str, str)
    devices_changed = Signal(object)
    device_refresh_failed = Signal(str)
    connection_finished = Signal(bool, str)
    roi_layout_screenshot_ready = Signal(str, object)
    roi_layout_screenshot_failed = Signal(str, str)

    def __init__(self, config=None, capture_service=None, ocr_service=None,
                 operation_service=None, parent=None, roi_config=None, **kwargs):
        super().__init__(parent)
        self.config = dict(config or {})
        self.emulator_type = "mumu"
        self.calls: list = []

    def update_adb_path(self, path: str) -> None:
        self.config["mumu_adb_path"] = path
        self.calls.append(("update_adb_path", path))

    def detect_adb(self, emulator_type: str = "mumu") -> None:
        self.calls.append(("detect_adb",))

    def refresh_devices(self, emulator_type: str = "mumu") -> None:
        self.calls.append(("refresh_devices",))

    def connect(self, device, selected_explicitly: bool) -> str:
        self.calls.append(("connect", device))
        return ""

    def start_roi_layout_capture(self, page_type: str) -> bool:
        self.calls.append(("roi_capture", page_type))
        return True

    def finish_roi_layout_capture(self, page_type: str) -> None:
        self.calls.append(("finish_roi", page_type))

    def roi_layout(self, page_type: str):
        return SimpleNamespace(reference_size=[2560, 1440])

    def shutdown(self) -> None:
        self.calls.append(("shutdown",))


class _NoopEnvWorker(QObject):
    result_ready = Signal(object)
    failed = Signal(str)

    def start(self) -> None:
        pass


class _FakeWindow(QWidget):
    def __init__(self, capture: _FakeCaptureService, data) -> None:
        super().__init__()
        self._capture_service = capture
        self._ocr_service = SimpleNamespace()
        self._data = data
        self._dialogs = SimpleNamespace(_data_error=None, open_mumu_config=self._open_mumu)
        self._reporter = SimpleNamespace(show_event_message=lambda m: None)
        self.open_calls: list[str] = []
        self.reporter_events: list[str] = []
        self._reporter.show_event_message = self.reporter_events.append

    def _open_mumu(self) -> None:
        self.open_calls.append("opened")


def _fake_data(issues=()):
    return SimpleNamespace(
        last_load_report=SimpleNamespace(issues=list(issues)),
        heroes=SimpleNamespace(list_heroes=lambda: [SimpleNamespace(name="甲"),
                                                    SimpleNamespace(name="乙")]),
    )


def _device(port=16384, running=True, name="MuMu-0"):
    return SimpleNamespace(is_running=running, adb_port=port, name=name, index="0")


@pytest.fixture
def wizard(monkeypatch, tmp_path):
    """向导 + 全套 fake：EnvCheckWorker 静默、协调器/状态写入全部可观测。"""
    app = QApplication.instance() or QApplication([])
    capture = _FakeCaptureService()
    window = _FakeWindow(capture, _fake_data())
    monkeypatch.setattr(onboarding_dialog, "EnvCheckWorker", _NoopEnvWorker)
    monkeypatch.setattr(onboarding_dialog, "MumuConfigCoordinator", _FakeCoordinator)
    monkeypatch.setattr(onboarding_dialog, "get_mumu_config",
                        lambda: {"mumu_adb_path": "C:/adb.exe"})
    steps: list[str] = []
    skips: list[bool] = []
    monkeypatch.setattr(onboarding_dialog, "mark_step", lambda s: steps.append(s))
    monkeypatch.setattr(onboarding_dialog, "mark_skipped", lambda: skips.append(True))
    dialog = OnboardingDialog(window)
    try:
        yield SimpleNamespace(dialog=dialog, capture=capture, window=window,
                              steps=steps, skips=skips)
    finally:
        dialog.close()
        dialog.deleteLater()
        window.deleteLater()
        app.processEvents()


# ── T4：骨架与段 1 ──────────────────────────────────────────────────

def test_dialog_constructs_with_three_sections(wizard):
    d = wizard.dialog
    assert d.windowTitle() == "首启引导"
    assert d._env_badge.text() == "检查中"  # 构造即自动开跑段 1
    assert d._device_badge.text() == "待检"
    # 引擎就绪态：段 3 徽标就绪，识别按钮可用
    assert d._engine_badge.text() == "就绪"
    assert d._recognize_button.isEnabled()


def test_env_findings_rendered_with_data_states(wizard):
    d = wizard.dialog
    from src.scripts import doctor as doctor_mod
    findings = [
        Finding("环境", "onnxruntime 版本", doctor_mod.PASS, "1.23.2"),
        Finding("环境", "OCR 模型", doctor_mod.WARN, "v4 缺失", "python -m src.scripts.fetch_recheck_models 预取 v4"),
        Finding("配置", "死键", doctor_mod.FAIL, "RAG_PROJECT_DIR", "删除该键"),
        Finding("RAG索引", "st 后端", doctor_mod.SKIP, "跳过项"),
    ]
    d._on_env_checks_done(findings)

    rows = [d._env_container.itemAt(i).widget() for i in range(d._env_container.count())]
    assert len(rows) == 4  # 3 条 finding + 1 条数据行；SKIP 折叠不渲染
    from PySide6.QtWidgets import QPushButton
    fix_buttons = [b for w in rows for b in w.findChildren(QPushButton)]
    assert len(fix_buttons) == 2  # WARN 与 FAIL 行各一个修复按钮
    assert d._env_badge.text() == "未通过"  # 含 FAIL
    assert "2 项通过 / 1 项注意 / 1 项需处理" in d._env_summary.text()
    assert d._env_button.isEnabled()


def test_env_data_failure_state_visible(wizard):
    d = wizard.dialog
    wizard.window._dialogs._data_error = "heroes.json 解析失败"
    d._on_env_checks_done([])
    rows_text = []
    from PySide6.QtWidgets import QLabel as _QLabel
    for i in range(d._env_container.count()):
        w = d._env_container.itemAt(i).widget()
        rows_text.append(" ".join(lbl.text() for lbl in w.findChildren(_QLabel)))
    assert any("加载失败" in t for t in rows_text)
    assert d._env_badge.text() == "未通过"


def test_skip_writes_state_and_rejects(wizard):
    wizard.dialog._on_skipped()
    assert wizard.skips == [True]
    from PySide6.QtWidgets import QDialog as _QDialog
    assert wizard.dialog.result() == _QDialog.DialogCode.Rejected


# ── T5：段 2 连接与分辨率体检 ────────────────────────────────────────

def test_device_zero_running_shows_retry(wizard):
    d = wizard.dialog
    coordinator = d._ensure_coordinator()
    coordinator.devices_changed.emit(())
    assert d._device_badge.text() == "未通过"
    assert "请先启动 MuMu 模拟器" in d._device_summary.text()
    assert d._device_action.text() == "重试"
    assert not d._device_action.isHidden()


def test_device_single_running_auto_connects_and_captures(wizard):
    d = wizard.dialog
    coordinator = d._ensure_coordinator()
    coordinator.devices_changed.emit((_device(),))
    assert any(c[0] == "connect" for c in coordinator.calls)

    coordinator.connection_finished.emit(True, "已连接")
    assert wizard.steps == ["device"]
    assert ("roi_capture", "hero_selection") in coordinator.calls
    assert d._device_summary.text() == "已连接，正在体检画面分辨率…"


def test_device_multiple_running_shows_combo_without_auto_connect(wizard):
    d = wizard.dialog
    coordinator = d._ensure_coordinator()
    coordinator.devices_changed.emit((_device(16384, name="MuMu-0"), _device(16416, name="MuMu-1")))
    assert not d._device_combo.isHidden()
    assert d._device_combo.count() == 2
    assert not any(c[0] == "connect" for c in coordinator.calls)  # 多台不擅自选择
    assert "请选择" in d._device_summary.text()


def test_resolution_warn_shows_reframe_link(wizard):
    d = wizard.dialog
    coordinator = d._ensure_coordinator()
    coordinator.roi_layout_screenshot_ready.emit(
        "hero_selection", SimpleNamespace(width=1920, height=1080))
    assert d._device_badge.text() == "注意"
    assert "等比缩放" in d._device_summary.text()
    assert d._device_action.text() == "去重新框选"
    assert not d._device_action.isHidden()


def test_resolution_portrait_fail(wizard):
    d = wizard.dialog
    coordinator = d._ensure_coordinator()
    coordinator.roi_layout_screenshot_ready.emit(
        "hero_selection", SimpleNamespace(width=1080, height=1920))
    assert d._device_badge.text() == "未通过"
    assert "竖屏" in d._device_summary.text()


def test_roi_screenshot_ready_releases_capture_slot(wizard):
    """回归 VUL-B4-004：体检截图后必须释放 in-progress 槽位。

    协调器只负责发信号，收尾由消费方调用；向导不进入 ROI 编辑态，漏掉
    finish 会让 "hero_selection" 永久留在集合里，此后每次「连接并体检」
    都被 start_roi_layout_capture 拒绝，界面静默停在"正在体检"。
    """
    d = wizard.dialog
    coordinator = d._ensure_coordinator()
    d._on_connection_finished(True, "已连接")
    assert ("roi_capture", "hero_selection") in coordinator.calls

    coordinator.roi_layout_screenshot_ready.emit(
        "hero_selection", SimpleNamespace(width=2560, height=1440))
    assert ("finish_roi", "hero_selection") in coordinator.calls

    # 槽位已释放：第二次体检能再次发起截图
    calls_before = len([c for c in coordinator.calls if c[0] == "roi_capture"])
    d._on_connection_finished(True, "已连接")
    calls_after = len([c for c in coordinator.calls if c[0] == "roi_capture"])
    assert calls_after == calls_before + 1


def test_roi_screenshot_ready_releases_slot_even_on_verdict_error(wizard):
    """判定过程抛异常也必须释放槽位，否则一次坏帧就锁死后续体检。

    直接调用槽函数而非 emit 信号：PySide6 在信号发射期间调用 Python 槽时，
    槽内异常经 sys.excepthook 打印后被吞掉，emit() 正常返回，pytest.raises
    捕获不到（生产环境同样如此，故异常只能靠 finally 兜住收尾）。
    """
    d = wizard.dialog
    coordinator = d._ensure_coordinator()
    coordinator.roi_layout = lambda _page: (_ for _ in ()).throw(RuntimeError("ROI 读取失败"))

    with pytest.raises(RuntimeError):
        d._on_roi_screenshot_ready(
            "hero_selection", SimpleNamespace(width=2560, height=1440))

    assert ("finish_roi", "hero_selection") in coordinator.calls


def test_roi_screenshot_failed_shows_retry(wizard):
    d = wizard.dialog
    coordinator = d._ensure_coordinator()
    coordinator.roi_layout_screenshot_failed.emit("hero_selection", "ADB 会话失效")
    assert "ADB 会话失效" in d._device_summary.text()
    assert d._device_action.text() == "重试"


def test_deep_link_reruns_section2(wizard):
    d = wizard.dialog
    coordinator = d._ensure_coordinator()
    d._open_mumu_config()
    assert wizard.window.open_calls == ["opened"]
    # 旧协调器整只退役（半刷新会残留旧端口草稿，把共享会话倒退）
    assert ("shutdown",) in coordinator.calls
    assert d._coordinator is not coordinator
    # 新协调器以新配置快照重建并自动重跑段 2
    assert d._coordinator.calls == [("refresh_devices",)]


def test_deep_link_after_port_change_uses_fresh_config(wizard, monkeypatch):
    """多实例换端口场景（P1 回归）：深链保存新端口后，重建的协调器草稿必须是新值。"""
    d = wizard.dialog
    d._ensure_coordinator()
    # 深链期间配置对话框保存了新端口（open_mumu_config 推共享会话 + persist）
    monkeypatch.setattr(onboarding_dialog, "get_mumu_config",
                        lambda: {"mumu_adb_path": "C:/adb.exe", "mumu_adb_port": 16416})
    d._open_mumu_config()

    fresh = d._coordinator
    assert fresh is not None
    assert fresh.config.get("mumu_adb_port") == 16416  # 不再是构造时的旧端口


def test_close_event_marks_closing_before_shutdown(wizard):
    d = wizard.dialog
    coordinator = d._ensure_coordinator()
    order: list = []
    coordinator.shutdown = lambda: order.append(("shutdown", d._closing))
    d.close()
    assert order == [("shutdown", True)]
    assert d._closing is True


# ── T6：段 3 门禁 / 悬挂复位 / 结果分派 ─────────────────────────────

def test_warming_disables_button_but_failed_enables(wizard):
    d = wizard.dialog
    wizard.capture.ocr_warmup_state_changed.emit("warming", "")
    assert not d._recognize_button.isEnabled()
    assert "加载中" in d._engine_summary.text()
    wizard.capture.ocr_warmup_state_changed.emit("failed", "模型损坏")
    assert d._recognize_button.isEnabled()


def test_warmup_hang_shows_reset_and_requeues(wizard):
    d = wizard.dialog
    wizard.capture._state = "warming"  # fake 属性与信号同步（真服务两者同源）
    wizard.capture.ocr_warmup_state_changed.emit("warming", "")
    d._on_warmup_hang()  # 不等真实 150 秒，直接到点
    assert d._recognize_button.isEnabled()
    assert d._recognize_button.text() == "重新初始化"
    assert "超时" in d._engine_summary.text()

    d._start_recognize()  # 悬挂态点击 = 复位 + 重新入队，不发识别
    assert wizard.capture.ocr_coordinator.reset_calls == 1
    assert len(wizard.capture.warmup_calls) == 1
    assert wizard.capture.do_capture_calls == []


def test_capture_success_marks_step_and_reports(wizard):
    d = wizard.dialog
    d._start_recognize()
    assert wizard.capture.do_capture_calls == [
        {"hero_names": ["甲", "乙"], "force_ocr": True}]
    assert "正在识别" in d._recognize_summary.text()

    wizard.capture.capture_completed.emit(
        {"image": None, "save_path": None, "ocr_results": ["甲", "乙"], "ocr_matched": True})
    assert "识别到 2 个武将" in d._recognize_summary.text()
    assert wizard.steps == ["recognize"]
    assert wizard.window.reporter_events == ["首次识别成功"]
    assert not d._recognize_button.isEnabled()  # 成功后不再重复识别


def test_capture_template_miss_prompts_hero_page(wizard):
    d = wizard.dialog
    d._start_recognize()
    wizard.capture.capture_completed.emit(
        {"image": None, "save_path": None, "ocr_results": None, "ocr_matched": False})
    assert "选将" in d._recognize_summary.text()
    assert d._recognize_button.isEnabled()


def test_capture_engine_unavailable(wizard):
    d = wizard.dialog
    d._start_recognize()
    wizard.capture._state = "failed"
    wizard.capture.capture_completed.emit(
        {"image": None, "save_path": None, "ocr_results": None, "ocr_matched": False})
    assert "识别引擎当前不可用" in d._recognize_summary.text()


def test_capture_failed_dispatches_error(wizard):
    d = wizard.dialog
    d._start_recognize()
    wizard.capture.capture_failed.emit("ADB 未连接")
    assert "ADB 未连接" in d._recognize_summary.text()
    assert d._recognize_button.isEnabled()


# ---------------------------------------------------------------------------
# T6：预热悬挂复位（真 OcrTaskCoordinator 状态机，假任务）
# ---------------------------------------------------------------------------

def test_ocr_task_coordinator_reset_warmup_allows_requeue():
    import threading

    from src.business.emulator.ocr_task_coordinator import OcrTaskCoordinator

    coordinator = OcrTaskCoordinator(on_worker_created=lambda worker: None)
    # 模拟悬挂：在途任务永不完成（completed 不 set），状态停在 warming
    coordinator.note_warmup_submitted(SimpleNamespace(completed=threading.Event()))
    assert coordinator.warmup_state == "warming"

    coordinator.reset_warmup()
    assert coordinator.warmup_state == "idle"
    assert coordinator._warmup_task is None

    # 复位后可重新入队（悬挂时 warmup_ocr_model 是 no-op，这是唯一出口）
    coordinator.note_warmup_submitted(SimpleNamespace(completed=threading.Event()))
    assert coordinator.warmup_state == "warming"

    coordinator.reset_warmup()
    coordinator.reset_warmup()  # 幂等：已复位时不重复发状态信号


def test_ocr_task_coordinator_reset_warmup_releases_worker_flag():
    """回归（模拟器域 VUL-B4-002）：复位须传递到 worker 的预热入队标志。

    协调器状态拨回 idle 但 worker 标志悬挂时，warmup_model 仍恒返回
    None，重试永久无效——首启向导「重新初始化」经此出口恢复可预热。
    """
    from src.business.emulator.ocr_task_coordinator import OcrTaskCoordinator
    from src.business.recognition.ocr_worker import OcrWorker

    coordinator = OcrTaskCoordinator(on_worker_created=lambda worker: None)
    worker = OcrWorker()
    coordinator._worker = worker
    worker._warmup_queued = True  # 模拟弃单/滞留队列的预热标志悬挂

    coordinator.reset_warmup()

    assert worker._warmup_queued is False


# ---------------------------------------------------------------------------
# T7：open_onboarding 接线
# ---------------------------------------------------------------------------

def test_open_onboarding_execs_and_syncs_poll(monkeypatch):
    from src.ui.app import dialog_coordinator as dc_module

    calls = []

    class _StubDialog:
        def __init__(self, parent):
            calls.append(("construct", parent))

        def exec(self):
            calls.append(("exec",))
            return 0

    monkeypatch.setattr("src.ui.app.onboarding_dialog.OnboardingDialog", _StubDialog)
    window = SimpleNamespace(_poll_coordinator=SimpleNamespace(
        sync_with_connection=lambda: calls.append(("sync",))))
    dc_module.DialogCoordinator(window).open_onboarding()

    assert calls == [("construct", window), ("exec",), ("sync",)]


def test_open_onboarding_protects_against_dialog_errors(monkeypatch):
    """向导异常不得向外传播（spec R1）：主入口 try 内异常会演变为启动失败。"""
    from src.ui.app import dialog_coordinator as dc_module

    class _BoomDialog:
        def __init__(self, parent):
            raise RuntimeError("向导构造炸了")

    monkeypatch.setattr("src.ui.app.onboarding_dialog.OnboardingDialog", _BoomDialog)
    warned: list = []
    monkeypatch.setattr(dc_module.QMessageBox, "warning",
                        lambda *args, **kwargs: warned.append(args))
    synced: list = []
    window = SimpleNamespace(_poll_coordinator=SimpleNamespace(
        sync_with_connection=lambda: synced.append(True)))

    dc_module.DialogCoordinator(window).open_onboarding()  # 不抛

    assert warned  # 用户得到一次告警而非崩溃/退出
    assert synced == [True]  # 轮询收尾照常执行


# ---------------------------------------------------------------------------
# T9：frozen 文案映射（打包版不出现开发者命令，一键项映射到 -m 动作）
# ---------------------------------------------------------------------------

from src.ui.app.onboarding_fixes import fix_action, sanitize_runtime_detail


@pytest.mark.parametrize("fix,expected_button,expected_module", [
    ("python -m src.scripts.fetch_recheck_models 预取 v4；v6 缺失则重装 rapidocr==3.9.2",
     "预取模型", "src.scripts.fetch_recheck_models"),
    ("python -m src.rag.indexer 重建索引（体积超限）", "重建索引", "src.rag.indexer"),
    ("python -m src.scripts.export_embedding_onnx 导出编码器", "导出编码器",
     "src.scripts.export_embedding_onnx"),
])
def test_fix_action_one_click_maps_module(fix, expected_button, expected_module):
    button, module, _tooltip = fix_action(fix, frozen=False)
    assert button == expected_button
    assert module == expected_module
    assert "python -m" not in button  # 按钮文案不暴露命令


@pytest.mark.parametrize("fix", [
    "按 environment.yml 安装 onnxruntime==1.23.2（OCR 引擎依赖）",
    "OCR 行为对 rapidocr 小版本敏感，安装 rapidocr==3.9.2",
    "python -m src.scripts.pull_data 同步工作区",
])
def test_fix_action_frozen_hides_dev_commands(fix):
    button, module, _tooltip = fix_action(fix, frozen=True)
    # frozen 下安装/命令类不渲染修复按钮（原文仅留在 tooltip 详情供反馈开发者，
    # spec §3.3 折叠区规则）；按钮为空即用户可见面无任何开发者命令
    assert button == ""
    assert module == ""


def test_fix_action_dev_mode_keeps_original(fix=None):
    button, module, tooltip = fix_action(
        "按 environment.yml 安装 onnxruntime==1.23.2", frozen=False)
    assert button == "查看修复建议"
    assert module == ""
    assert "environment.yml" in tooltip  # 开发态原文可见


def test_sanitize_runtime_detail_strips_dev_hint():
    detail = "rapidocr 模型缺失: ['rec.onnx']；请先运行 python -m src.scripts.fetch_recheck_models"
    frozen_text = sanitize_runtime_detail(detail, frozen=True)
    assert "python -m" not in frozen_text
    assert "预取" in frozen_text
    # 开发态原样保留
    assert sanitize_runtime_detail(detail, frozen=False) == detail


def test_wizard_fix_button_runs_module(wizard, monkeypatch):
    d = wizard.dialog
    launched: list[str] = []
    monkeypatch.setattr(d, "_run_fix_module", launched.append)
    d._append_finding_row(
        "WARN", "OCR 模型", "v4 缺失",
        "python -m src.scripts.fetch_recheck_models 预取 v4")
    from PySide6.QtWidgets import QPushButton
    buttons = d._env_container.itemAt(0).widget().findChildren(QPushButton)
    assert len(buttons) == 1
    assert buttons[0].text() == "预取模型"
    buttons[0].click()
    assert launched == ["src.scripts.fetch_recheck_models"]


# ---------------------------------------------------------------------------
# 真线程冒烟：EnvCheckWorker 起真 QThread（此前 Noop 桩静默了该路径，
# 漏掉"局部变量 GC 析构 running QThread"的闪退，2026-10-13 实测回归）
# ---------------------------------------------------------------------------

def test_env_check_worker_real_thread_survives_gc_window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    captured: list = []
    monkeypatch.setattr(
        onboarding_dialog, "run_checks",
        lambda offline=False, checks=None: captured.append("ran") or [
            Finding("环境", "真线程检查", "PASS", "ok")])

    worker = onboarding_dialog.EnvCheckWorker()  # 真 QThread，非 Noop 桩
    assert worker in onboarding_dialog.LIVE_WORKERS  # 构造即持有，无 GC 窗口
    results: list = []
    worker.result_ready.connect(results.extend)
    worker.start()
    # 局部引用已交由 LIVE_WORKERS 持有；等线程收尾（结束前引用不得回收）
    assert worker.wait(10_000)

    for _ in range(50):
        app.processEvents()
        if results:
            break
    assert captured == ["ran"]
    assert results and results[0].name == "真线程检查"
    worker.deleteLater()
    app.processEvents()


def test_dialog_starts_real_worker_without_crash(monkeypatch):
    """向导构造即自动起真线程段 1（闪退场景的最小复现），跑完渲染不崩。"""
    app = QApplication.instance() or QApplication([])
    capture = _FakeCaptureService()
    window = _FakeWindow(capture, _fake_data())
    monkeypatch.setattr(
        onboarding_dialog, "run_checks",
        lambda offline=False, checks=None: [Finding("环境", "依赖", "PASS", "ok")])
    monkeypatch.setattr(onboarding_dialog, "MumuConfigCoordinator", _FakeCoordinator)
    monkeypatch.setattr(onboarding_dialog, "get_mumu_config",
                        lambda: {"mumu_adb_path": ""})

    dialog = OnboardingDialog(window)
    try:
        import time as _time
        started = _time.monotonic()
        while _time.monotonic() - started < 10:
            app.processEvents()
            if dialog._env_badge.text() in {"通过", "注意", "未通过", "检查失败"}:
                break
            _time.sleep(0.02)
        assert dialog._env_badge.text() == "通过"
        assert dialog._env_button.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        window.deleteLater()
        app.processEvents()
