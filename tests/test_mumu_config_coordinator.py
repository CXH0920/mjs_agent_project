"""模拟器配置协调器的行为测试。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from src.business.emulator.mumu_config_coordinator import MumuConfigCoordinator, persist_mumu_env_config
from src.capture.probers import EmulatorDeviceInfo
from src.ocr.roi_config import DEFAULT_ROI_CONFIG_PATH, OcrRoiConfig


class _CaptureService(QObject):
    connection_changed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.connection_state = ("disconnected", "")
        self.capture = object()
        self.configs: list[dict] = []
        self.target_ports: list[int] = []

    def update_config(self, config: dict) -> None:
        self.configs.append(dict(config))

    def set_target_port(self, port: int) -> None:
        self.target_ports.append(port)


class _OcrService:
    poll_state = "stopped"

    def __init__(self, template_path: Path) -> None:
        self._template_path = template_path
        self.loaded: set[str] = set()
        self.created: list[tuple[str, tuple[int, int, int, int]]] = []
        self.selected: list[tuple[str, str]] = []
        self.resume_count = 0

    def is_template_loaded(self, template_name: str = "hero_selection") -> bool:
        return template_name in self.loaded

    def template_path(self, template_name: str = "hero_selection") -> Path:
        return self._template_path / f"{template_name}.png"

    def create_template(self, _image, roi: tuple[int, int, int, int], template_name: str) -> None:
        self.created.append((template_name, roi))
        self.loaded.add(template_name)

    def select_template(self, file_path: str, template_name: str = "hero_selection") -> None:
        self.selected.append((file_path, template_name))
        self.loaded.add(template_name)

    def resume_poll(self) -> None:
        self.resume_count += 1


class _OperationService(QObject):
    adb_detected = Signal(bool, str, str)
    devices_refreshed = Signal(object)
    device_refresh_failed = Signal(str)
    connection_finished = Signal(bool, str)
    disconnection_finished = Signal(bool, str)
    device_tested = Signal(bool, str, str)
    screenshot_ready = Signal(str, object)
    screenshot_failed = Signal(str, str)
    operation_failed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.connect_count = 0
        self.refresh_count = 0
        self.template_requests: list[str] = []
        self.test_requests: list[tuple[str, int]] = []
        self.closed = False

    def detect_adb(self, emulator_type: str = "mumu") -> None:
        pass

    def refresh_devices(self, emulator_type: str = "mumu") -> None:
        self.refresh_count += 1

    def connect(self) -> None:
        self.connect_count += 1

    def disconnect(self) -> None:
        pass

    def test_device(self, path: str, port: int) -> None:
        self.test_requests.append((path, port))

    def capture_template_screenshot(self, template_name: str) -> None:
        self.template_requests.append(template_name)

    def shutdown(self) -> None:
        self.closed = True


def _coordinator(tmp_path: Path) -> tuple[MumuConfigCoordinator, _CaptureService, _OcrService, _OperationService]:
    capture = _CaptureService()
    ocr = _OcrService(tmp_path)
    operation = _OperationService()
    coordinator = MumuConfigCoordinator(
        {"emulator_adb_path": "adb.exe", "emulator_adb_port": 0},
        capture,
        ocr,
        operation,
    )
    return coordinator, capture, ocr, operation


def test_connect_and_save_require_explicit_multi_instance_selection(tmp_path: Path) -> None:
    coordinator, capture, _ocr, operation = _coordinator(tmp_path)
    devices = [
        EmulatorDeviceInfo("1", "实例 A", 16448, True),
        EmulatorDeviceInfo("2", "实例 B", 16416, True),
    ]
    operation.devices_refreshed.emit(devices)

    assert "多个运行中的模拟器实例" in coordinator.connect(None, False)
    config, error = coordinator.save_config({}, None, False)
    assert config is None
    assert "多个运行中的模拟器实例" in error

    assert coordinator.connect(devices[1], True) == ""
    config, error = coordinator.save_config({}, devices[1], True)

    assert error == ""
    assert capture.target_ports == [16416]
    assert operation.connect_count == 1
    assert config["emulator_adb_port"] == 16416


def test_save_config_port_merge_rules(tmp_path: Path) -> None:
    """特征测试：锚定 7e88f63「多实例明确选择策略」的端口保存规则。

    ① 显式选择 → 用所选设备端口；② 自动模式（配置端口 0）→ 保持 0；
    ③ 固定端口 + 设备下拉框现存设备 → 跟随该设备端口。UI 刷新下拉框时
    优先高亮端口匹配项，③ 仅在配置端口对应设备已消失时才实际改写值，
    属有意设计而非缺陷（考古见 7e88f63 提交说明"保存规则"）。
    """
    coordinator, _capture, _ocr, _operation = _coordinator(tmp_path)
    device = EmulatorDeviceInfo("2", "实例 B", 16416, True)

    # ① 显式选择：端口取所选设备
    config, error = coordinator.save_config({"emulator_adb_path": "adb.exe"}, device, True)
    assert error == ""
    assert config["emulator_adb_port"] == 16416

    # ② 自动模式（端口 0）：保持自动探测，不被传入设备覆盖
    coordinator._config["emulator_adb_port"] = 0
    config, _error = coordinator.save_config({"emulator_adb_path": "adb.exe"}, device, False)
    assert config["emulator_adb_port"] == 0

    # ③ 固定端口 + 未显式选择：跟随现存设备端口
    coordinator._config["emulator_adb_port"] = 16384
    config, _error = coordinator.save_config({"emulator_adb_path": "adb.exe"}, device, False)
    assert config["emulator_adb_port"] == 16416


def test_template_lifecycle_and_service_delegation(tmp_path: Path) -> None:
    coordinator, _capture, ocr, operation = _coordinator(tmp_path)
    finished: list[str] = []
    coordinator.template_capture_finished.connect(finished.append)

    assert coordinator.start_template_capture("match_guide")
    assert not coordinator.start_template_capture("match_guide")
    assert coordinator.is_template_capture_in_progress("match_guide")
    assert operation.template_requests == ["match_guide"]

    coordinator.create_template(object(), (1, 2, 3, 4), "match_guide")
    coordinator.finish_template_capture("match_guide")
    coordinator.select_template("chosen.png")

    assert ocr.created == [("match_guide", (1, 2, 3, 4))]
    assert ocr.selected == [("chosen.png", "hero_selection")]
    assert coordinator.template_status("match_guide").loaded
    assert finished == ["match_guide"]
    assert not coordinator.is_template_capture_in_progress("match_guide")


def test_roi_layout_capture_and_save_are_separate_from_template_lifecycle(tmp_path: Path) -> None:
    capture = _CaptureService()
    ocr = _OcrService(tmp_path)
    operation = _OperationService()
    roi_config = OcrRoiConfig(DEFAULT_ROI_CONFIG_PATH, tmp_path / "ocr_rois.json")
    coordinator = MumuConfigCoordinator(
        {"emulator_adb_path": "adb.exe", "emulator_adb_port": 0},
        capture,
        ocr,
        operation,
        roi_config=roi_config,
    )
    received: list[tuple[str, object]] = []
    coordinator.roi_layout_screenshot_ready.connect(lambda page, image: received.append((page, image)))

    assert coordinator.start_roi_layout_capture("hero_selection")
    assert not coordinator.start_roi_layout_capture("hero_selection")
    assert operation.template_requests == ["roi_layout:hero_selection"]
    operation.screenshot_ready.emit("roi_layout:hero_selection", "image")
    coordinator.finish_roi_layout_capture("hero_selection")

    layout = coordinator.roi_layout("hero_selection")
    coordinator.save_roi_layout("hero_selection", layout)
    assert received == [("hero_selection", "image")]
    assert roi_config.user_path.exists()


def test_shutdown_disconnects_connection_state_relay(tmp_path: Path) -> None:
    """对话框关闭后，挂在常驻 CaptureService 上的连接状态中继必须解除。

    signal.emit 形式的中继连接没有 QObject receiver，不随对话框销毁自动
    清理；残留连接会在下次 ADB 状态广播时对已销毁对话框抛
    "Signal source has been deleted"（2026-10-07 17:50 实测两条 ERROR）。
    """
    coordinator, capture, _ocr, _operation = _coordinator(tmp_path)
    relayed: list[tuple[str, str]] = []
    coordinator.connection_state_changed.connect(
        lambda state, detail: relayed.append((state, detail)),
    )

    capture.connection_changed.emit("connected", "127.0.0.1:16448")
    assert relayed == [("connected", "127.0.0.1:16448")]

    coordinator.shutdown()
    capture.connection_changed.emit("disconnected", "")

    assert relayed == [("connected", "127.0.0.1:16448")]


def test_persist_mumu_env_config_writes_twelve_dialog_keys(monkeypatch) -> None:
    """env 持久化固定写对话框可编辑的 12 键（3 连接键 + 9 OCR 键），部署键不落盘。

    连接键自 2026-10 起写 EMULATOR_*（含品牌 EMULATOR_TYPE）；旧
    MUMU_ADB_* 不再镜像写入，残留行为惰性数据（读取经 default_from 回退）。
    """
    recorded: list[tuple[object, dict]] = []
    monkeypatch.setattr(
        "src.business.emulator.mumu_config_coordinator.save_env_file",
        lambda path, data: recorded.append((path, dict(data))),
    )

    persist_mumu_env_config({
        "emulator_type": "ldplayer",
        "emulator_adb_path": "adb.exe",
        "emulator_adb_port": 16384,
        "mumu_ocr_enabled": True,
        "mumu_ocr_poll_mode": False,
        "mumu_ocr_poll_idle_pause": True,
        "mumu_ocr_auto_switch_tab": True,
        "mumu_ocr_poll_interval": 3,
        "mumu_ocr_match_threshold": 0.82,
        "mumu_hero_selection_threshold": 0.9,
        "mumu_hero_selection_cooldown": 120,
        "mumu_match_guide_threshold": 0.85,
        "mumu_ocr_use_gpu": True,
        "mumu_ocr_cpu_threads": 8,
        "mumu_ocr_recheck_enabled": True,
    })

    assert len(recorded) == 1
    path, data = recorded[0]
    assert data == {
        "EMULATOR_TYPE": "ldplayer",
        "EMULATOR_ADB_PATH": "adb.exe",
        "EMULATOR_ADB_PORT": "16384",
        "MUMU_OCR_ENABLED": "true",
        "MUMU_OCR_POLL_MODE": "false",
        "MUMU_OCR_POLL_IDLE_PAUSE": "true",
        "MUMU_OCR_AUTO_SWITCH_TAB": "true",
        "MUMU_OCR_POLL_INTERVAL": "3",
        "MUMU_OCR_MATCH_THRESHOLD": "0.82",
        "MUMU_HERO_SELECTION_THRESHOLD": "0.9",
        "MUMU_HERO_SELECTION_COOLDOWN": "120",
        "MUMU_MATCH_GUIDE_THRESHOLD": "0.85",
    }


def test_connect_adopts_auto_highlighted_device_port_across_brands(tmp_path: Path) -> None:
    """跨品牌连接：配置草稿仍是旧品牌端口时，自动高亮的唯一实例（非显式
    选择）的端口必须生效——否则会连去旧品牌端口（夜神真机实测 2026-10-24）。
    """
    coordinator, capture, _ocr, operation = _coordinator(tmp_path)
    coordinator._config["emulator_adb_port"] = 16448  # 旧品牌（MuMu）残留端口
    device = EmulatorDeviceInfo("1", "Nox_1", 62025, True)
    operation.devices_refreshed.emit([device])  # 唯一运行实例被自动高亮

    assert coordinator.connect(device, selected_explicitly=False) == ""

    assert capture.target_ports == [62025]
    assert operation.connect_count == 1
