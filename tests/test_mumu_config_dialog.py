"""MuMu 设备选择与连通性测试。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QPushButton

from src.capture.prober import MuMuDeviceInfo
from src.ui.configuration.mumu_config_dialog import MumuConfigDialog
from src.ui.configuration.mumu_config_sections import MumuDeviceSection, MumuOcrPollingSection, MumuTemplateSection


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


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

    def __init__(self, devices: list[MuMuDeviceInfo]) -> None:
        super().__init__()
        self._devices = devices
        self.template_capture_requests: list[str] = []

    def refresh_devices(self) -> None:
        self.devices_refreshed.emit(self._devices)

    def detect_adb(self) -> None:
        raise AssertionError("已配置 ADB 时不应自动探测")

    def connect(self) -> None:
        raise AssertionError("此测试不应连接设备")

    def disconnect(self) -> None:
        raise AssertionError("此测试不应断开设备")

    def test_device(self, _adb_path: str, _port: int) -> None:
        raise AssertionError("此测试不应测试设备")

    def capture_template_screenshot(self, template_name: str) -> None:
        self.template_capture_requests.append(template_name)

    def shutdown(self) -> None:
        pass


class _OcrService:
    poll_state = "stopped"

    def __init__(self, path: Path) -> None:
        self._path = path
        self.created: list[tuple[str, tuple[int, int, int, int]]] = []
        self.loaded: set[str] = set()

    def is_template_loaded(self, template_name: str = "hero_selection") -> bool:
        return template_name in self.loaded

    def template_path(self, template_name: str = "hero_selection") -> Path:
        return self._path / f"{template_name}.png"

    def create_template(self, _image, roi: tuple[int, int, int, int], template_name: str) -> None:
        self.created.append((template_name, roi))
        self.loaded.add(template_name)

    def select_template(self, _path: str, template_name: str = "hero_selection") -> None:
        self.loaded.add(template_name)


def _dialog(config: dict, devices: list[MuMuDeviceInfo], ocr_service=None) -> MumuConfigDialog:
    return MumuConfigDialog(
        config,
        ocr_service=ocr_service,
        operation_service=_OperationService(devices),
    )


def test_unique_running_device_keeps_auto_port() -> None:
    _app()
    unique = MuMuDeviceInfo("1", "唯一实例", 16448, True)
    dialog = _dialog({"mumu_adb_path": "adb.exe", "mumu_adb_port": 0}, [unique])

    assert dialog._device_combo.currentData() == unique
    assert dialog._config["mumu_adb_port"] == 0


def test_auto_switch_tab_checkbox_loads_and_saves_config(monkeypatch) -> None:
    _app()
    dialog = _dialog(
        {"mumu_adb_path": "adb.exe", "mumu_adb_port": 0, "mumu_ocr_poll_mode": True,
         "mumu_ocr_auto_switch_tab": True},
        [],
    )

    assert dialog._auto_switch_tab_check.isChecked()
    assert dialog._auto_switch_tab_check.isEnabled()
    dialog._auto_switch_tab_check.setChecked(False)
    monkeypatch.setattr(dialog, "_show_save_toast", lambda: None)
    dialog._on_save()
    assert dialog.get_config()["mumu_ocr_auto_switch_tab"] is False


def test_ocr_roi_controls_keep_the_configuration_footer() -> None:
    _app()
    dialog = _dialog({"mumu_adb_path": "adb.exe", "mumu_adb_port": 0}, [])
    buttons = [button.text() for button in dialog.findChildren(QPushButton)]

    assert buttons.count("截图编辑") == 2
    assert buttons.count("图片编辑") == 2
    assert buttons.count("恢复默认") == 2
    assert "保存" in buttons


def test_dialog_composes_dedicated_configuration_sections() -> None:
    _app()
    dialog = _dialog({"mumu_adb_path": "adb.exe", "mumu_adb_port": 0}, [])

    assert isinstance(dialog._device_section, MumuDeviceSection)
    assert isinstance(dialog._template_section, MumuTemplateSection)
    assert isinstance(dialog._ocr_polling_section, MumuOcrPollingSection)
    assert [dialog._page_nav.item(index).text() for index in range(dialog._page_nav.count())] == [
        "设备与连接", "识别与自动化",
    ]
    assert dialog._page_stack.count() == 2
    assert dialog._page_stack.currentIndex() == 0
    assert dialog._ocr_enabled_check.text() == "启用 OCR 识别"


def test_multiple_running_devices_require_explicit_selection() -> None:
    _app()
    devices = [
        MuMuDeviceInfo("1", "实例 A", 16448, True),
        MuMuDeviceInfo("2", "实例 B", 16416, True),
    ]
    dialog = _dialog({"mumu_adb_path": "adb.exe", "mumu_adb_port": 0}, devices)

    assert dialog._device_combo.currentData() is None
    assert "请选择运行中的实例" in dialog._device_combo.currentText()


def test_saved_port_restores_matching_device() -> None:
    _app()
    devices = [
        MuMuDeviceInfo("1", "实例 A", 16448, True),
        MuMuDeviceInfo("2", "实例 B", 16416, True),
    ]
    dialog = _dialog({"mumu_adb_path": "adb.exe", "mumu_adb_port": 16416}, devices)

    assert dialog._device_combo.currentData() == devices[1]


def test_instance_status_color_follows_selected_device_state() -> None:
    _app()
    running = MuMuDeviceInfo("1", "运行实例", 16448, True)
    stopped = MuMuDeviceInfo("2", "停止实例", 16416, False)
    dialog = _dialog({"mumu_adb_path": "adb.exe", "mumu_adb_port": 16448}, [running, stopped])

    assert "#27ae60" in dialog._instance_status_label.styleSheet()
    assert "运行中" in dialog._instance_status_label.text()

    dialog._device_combo.setCurrentIndex(1)

    assert "#777" in dialog._instance_status_label.styleSheet()
    assert "未运行" in dialog._instance_status_label.text()


def test_refresh_failure_preserves_previous_device_selection() -> None:
    _app()
    device = MuMuDeviceInfo("1", "实例", 16448, True)
    dialog = _dialog({"mumu_adb_path": "adb.exe", "mumu_adb_port": 16448}, [device])

    dialog._operation_service.device_refresh_failed.emit("MuMuManager 查询失败")

    assert dialog._device_combo.currentData() == device
    assert dialog._port_label.text() == "16448"
    assert "保留上次结果" in dialog._instance_status_label.text()


def test_template_capture_remains_disabled_during_device_refresh() -> None:
    _app()
    device = MuMuDeviceInfo("1", "实例", 16448, True)
    dialog = _dialog({"mumu_adb_path": "adb.exe", "mumu_adb_port": 16448}, [device])

    dialog._start_template_capture("match_guide")
    dialog._operation_service.devices_refreshed.emit([device])

    assert dialog._make_match_guide_template_btn.text() == "正在截图..."
    assert not dialog._make_match_guide_template_btn.isEnabled()


def test_canceling_roi_does_not_create_template(tmp_path: Path, monkeypatch) -> None:
    _app()
    ocr_service = _OcrService(tmp_path)
    dialog = _dialog({"mumu_adb_path": "adb.exe", "mumu_adb_port": 0}, [], ocr_service)

    class _CancelDialog:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def exec(self):
            return QDialog.DialogCode.Rejected

        def get_roi(self):
            return None

    monkeypatch.setattr("src.ui.configuration.roi_selector.RoiSelectorDialog", _CancelDialog)
    dialog._on_template_screenshot_ready("hero_selection", Image.new("RGB", (80, 60)))
    dialog._on_template_screenshot_ready("match_guide", Image.new("RGB", (80, 60)))

    assert ocr_service.created == []
    assert dialog._make_template_btn.isEnabled()
    assert dialog._make_match_guide_template_btn.isEnabled()
    assert dialog._make_template_btn.text() == "制作模板"
    assert dialog._make_match_guide_template_btn.text() == "制作模板"


def test_recognition_page_reflows_without_horizontal_scroll() -> None:
    app = _app()
    device = MuMuDeviceInfo("1", "实例", 16448, True)
    dialog = _dialog(
        {"mumu_adb_path": "adb.exe", "mumu_adb_port": 16448, "mumu_ocr_poll_mode": True},
        [device],
    )
    dialog.resize(760, 620)
    dialog.show()
    dialog._page_nav.setCurrentRow(1)
    app.processEvents()

    recognition_scroll = dialog._page_stack.currentWidget()
    assert recognition_scroll.horizontalScrollBar().maximum() == 0
    assert dialog._template_section._compact_layout is True
    assert dialog._resume_poll_btn.isHidden()

    dialog.resize(900, 680)
    app.processEvents()
    assert recognition_scroll.horizontalScrollBar().maximum() == 0
    assert dialog._template_section._compact_layout is False
    dialog.hide()


def test_resume_poll_action_only_appears_while_paused(tmp_path: Path) -> None:
    _app()
    ocr_service = _OcrService(tmp_path)
    ocr_service.poll_state = "paused"
    dialog = _dialog(
        {"mumu_adb_path": "adb.exe", "mumu_adb_port": 0, "mumu_ocr_poll_mode": True},
        [],
        ocr_service,
    )

    assert not dialog._resume_poll_btn.isHidden()
    assert dialog._resume_poll_btn.isEnabled()

    dialog._poll_mode_check.setChecked(False)
    assert dialog._resume_poll_btn.isHidden()


def test_roi_layout_image_rejects_invalid_file(tmp_path: Path, monkeypatch) -> None:
    _app()
    invalid_path = tmp_path / "invalid.png"
    invalid_path.write_bytes(b"not an image")
    dialog = _dialog({"mumu_adb_path": "adb.exe", "mumu_adb_port": 0}, [])
    warnings: list[str] = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(invalid_path), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))
    monkeypatch.setattr(dialog, "_open_roi_layout_editor", lambda *_args: pytest.fail("不应打开编辑器"))

    dialog._select_roi_layout_image("hero_selection")

    assert warnings and "无法读取图片" in warnings[0]


def test_two_template_types_are_saved_through_ocr_service(tmp_path: Path, monkeypatch) -> None:
    _app()
    ocr_service = _OcrService(tmp_path)
    dialog = _dialog({"mumu_adb_path": "adb.exe", "mumu_adb_port": 0}, [], ocr_service)

    class _AcceptDialog:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def get_roi(self):
            return 10, 10, 30, 20

    monkeypatch.setattr("src.ui.configuration.roi_selector.RoiSelectorDialog", _AcceptDialog)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: None)

    image = Image.new("RGB", (80, 60))
    dialog._on_template_screenshot_ready("hero_selection", image)
    dialog._on_template_screenshot_ready("match_guide", image)

    assert ocr_service.created == [
        ("hero_selection", (10, 10, 30, 20)),
        ("match_guide", (10, 10, 30, 20)),
    ]
