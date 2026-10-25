"""MuMu 设备选择与连通性测试。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QPushButton, QToolTip
from src.business.emulator.capture_service import CaptureService
from src.business.recognition.ocr_service import OcrService
from src.capture.probers import EmulatorDeviceInfo
from src.ui.configuration.mumu_config_dialog import MumuConfigDialog
from src.ui.configuration.mumu_config_sections import MumuDeviceSection, MumuOcrPollingSection, MumuTemplateSection
from src.ui.shared.style import ROLE_PRIMARY, ROLE_SECONDARY
from src.ui.shared.widgets import DialogFooter


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _OperationService(QObject):
    adb_detected = Signal(bool, str, str)
    adb_reprobed = Signal(str)
    devices_refreshed = Signal(object)
    device_refresh_failed = Signal(str)
    connection_finished = Signal(bool, str)
    disconnection_finished = Signal(bool, str)
    device_tested = Signal(bool, str, str)
    screenshot_ready = Signal(str, object)
    screenshot_failed = Signal(str, str)
    operation_failed = Signal(str, str)

    def __init__(self, devices: list[EmulatorDeviceInfo]) -> None:
        super().__init__()
        self._devices = devices
        self.template_capture_requests: list[str] = []
        self.refresh_types: list[str] = []
        self.reprobe_types: list[str] = []

    def refresh_devices(self, emulator_type: str = "mumu") -> None:
        self.refresh_types.append(emulator_type)
        self.devices_refreshed.emit(self._devices)

    def reprobe_adb(self, emulator_type: str = "mumu") -> None:
        self.reprobe_types.append(emulator_type)

    def detect_adb(self, emulator_type: str = "mumu") -> None:
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


# 生产路径对话框配置出自 get_mumu_config() 全键字典；测试基线喂同等全键，用例可覆盖个别键
_FULL_MUMU_CONFIG = {
    "emulator_type": "mumu",
    "emulator_adb_path": "",
    "emulator_adb_port": 0,
    "mumu_ocr_enabled": False,
    "mumu_ocr_poll_mode": False,
    "mumu_ocr_auto_switch_tab": False,
    "mumu_ocr_poll_interval": 2,
    "mumu_ocr_match_threshold": 0.8,
    "mumu_hero_selection_threshold": 0.8,
    "mumu_hero_selection_cooldown": 180,
    "mumu_match_guide_threshold": 0.8,
    "mumu_ocr_use_gpu": False,
    "mumu_ocr_cpu_threads": 6,
}


def _dialog(config: dict, devices: list[EmulatorDeviceInfo], ocr_service=None) -> MumuConfigDialog:
    return MumuConfigDialog(
        {**_FULL_MUMU_CONFIG, **config},
        capture_service=CaptureService(),
        ocr_service=ocr_service or OcrService(),
        operation_service=_OperationService(devices),
    )


def test_unique_running_device_keeps_auto_port() -> None:
    _app()
    unique = EmulatorDeviceInfo("1", "唯一实例", 16448, True)
    dialog = _dialog({"emulator_adb_path": "adb.exe", "emulator_adb_port": 0}, [unique])

    assert dialog._device_page.device_combo.currentData() == unique
    assert dialog._config["emulator_adb_port"] == 0


def test_auto_switch_tab_checkbox_loads_and_saves_config(monkeypatch) -> None:
    _app()
    dialog = _dialog(
        {"emulator_adb_path": "adb.exe", "emulator_adb_port": 0, "mumu_ocr_poll_mode": True,
         "mumu_ocr_auto_switch_tab": True},
        [],
    )

    assert dialog._recognition_page.auto_switch_tab_check.isChecked()
    assert dialog._recognition_page.auto_switch_tab_check.isEnabled()
    dialog._recognition_page.auto_switch_tab_check.setChecked(False)
    monkeypatch.setattr(dialog, "_show_save_toast", lambda: None)
    dialog._on_save()
    assert dialog.get_config()["mumu_ocr_auto_switch_tab"] is False


def test_ocr_roi_controls_keep_the_configuration_footer() -> None:
    _app()
    dialog = _dialog({"emulator_adb_path": "adb.exe", "emulator_adb_port": 0}, [])
    buttons = [button.text() for button in dialog.findChildren(QPushButton)]

    assert buttons.count("截图编辑") == 2
    assert buttons.count("图片编辑") == 2
    assert buttons.count("恢复默认") == 2
    assert "保存" in buttons
    assert isinstance(dialog._footer, DialogFooter)
    assert dialog._footer.cancel_button.property("uiRole") == ROLE_SECONDARY
    assert dialog._footer.accept_button.property("uiRole") == ROLE_PRIMARY


def test_dialog_composes_dedicated_configuration_sections() -> None:
    _app()
    dialog = _dialog({"emulator_adb_path": "adb.exe", "emulator_adb_port": 0}, [])

    assert isinstance(dialog._device_page.section, MumuDeviceSection)
    assert isinstance(dialog._recognition_page.template_section, MumuTemplateSection)
    assert isinstance(dialog._recognition_page.ocr_polling_section, MumuOcrPollingSection)
    assert [dialog._page_nav.item(index).text() for index in range(dialog._page_nav.count())] == [
        "设备与连接", "识别与自动化",
    ]
    assert dialog._page_stack.count() == 2
    assert dialog._page_stack.currentIndex() == 0
    assert dialog._recognition_page.ocr_enabled_check.text() == "启用 OCR 识别"


def test_multiple_running_devices_require_explicit_selection() -> None:
    _app()
    devices = [
        EmulatorDeviceInfo("1", "实例 A", 16448, True),
        EmulatorDeviceInfo("2", "实例 B", 16416, True),
    ]
    dialog = _dialog({"emulator_adb_path": "adb.exe", "emulator_adb_port": 0}, devices)

    assert dialog._device_page.device_combo.currentData() is None
    assert "请选择运行中的实例" in dialog._device_page.device_combo.currentText()


def test_saved_port_restores_matching_device() -> None:
    _app()
    devices = [
        EmulatorDeviceInfo("1", "实例 A", 16448, True),
        EmulatorDeviceInfo("2", "实例 B", 16416, True),
    ]
    dialog = _dialog({"emulator_adb_path": "adb.exe", "emulator_adb_port": 16416}, devices)

    assert dialog._device_page.device_combo.currentData() == devices[1]


def test_instance_status_color_follows_selected_device_state() -> None:
    _app()
    running = EmulatorDeviceInfo("1", "运行实例", 16448, True)
    stopped = EmulatorDeviceInfo("2", "停止实例", 16416, False)
    dialog = _dialog({"emulator_adb_path": "adb.exe", "emulator_adb_port": 16448}, [running, stopped])

    assert "#27ae60" in dialog._device_page.instance_status_label.styleSheet()
    assert "运行中" in dialog._device_page.instance_status_label.text()

    dialog._device_page.device_combo.setCurrentIndex(1)

    assert "#777" in dialog._device_page.instance_status_label.styleSheet()
    assert "未运行" in dialog._device_page.instance_status_label.text()


def test_refresh_failure_preserves_previous_device_selection() -> None:
    _app()
    device = EmulatorDeviceInfo("1", "实例", 16448, True)
    dialog = _dialog({"emulator_adb_path": "adb.exe", "emulator_adb_port": 16448}, [device])

    dialog._operation_service.device_refresh_failed.emit("MuMuManager 查询失败")

    assert dialog._device_page.device_combo.currentData() == device
    assert dialog._device_page.port_label.text() == "16448"
    assert "保留上次结果" in dialog._device_page.instance_status_label.text()


def test_template_capture_remains_disabled_during_device_refresh() -> None:
    _app()
    device = EmulatorDeviceInfo("1", "实例", 16448, True)
    dialog = _dialog({"emulator_adb_path": "adb.exe", "emulator_adb_port": 16448}, [device])

    dialog._recognition_page.start_template_capture("match_guide")
    dialog._operation_service.devices_refreshed.emit([device])

    assert dialog._recognition_page.match_guide_make_button.text() == "正在截图..."
    assert not dialog._recognition_page.match_guide_make_button.isEnabled()


def test_canceling_roi_does_not_create_template(tmp_path: Path, monkeypatch) -> None:
    _app()
    ocr_service = _OcrService(tmp_path)
    dialog = _dialog({"emulator_adb_path": "adb.exe", "emulator_adb_port": 0}, [], ocr_service)

    class _CancelDialog:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def exec(self):
            return QDialog.DialogCode.Rejected

        def get_roi(self):
            return None

    monkeypatch.setattr("src.ui.configuration.roi_selector.RoiSelectorDialog", _CancelDialog)
    dialog._recognition_page._on_template_screenshot_ready("hero_selection", Image.new("RGB", (80, 60)))
    dialog._recognition_page._on_template_screenshot_ready("match_guide", Image.new("RGB", (80, 60)))

    assert ocr_service.created == []
    assert dialog._recognition_page.make_template_button.isEnabled()
    assert dialog._recognition_page.match_guide_make_button.isEnabled()
    assert dialog._recognition_page.make_template_button.text() == "制作模板"
    assert dialog._recognition_page.match_guide_make_button.text() == "制作模板"


def test_recognition_page_reflows_without_horizontal_scroll(tmp_path: Path) -> None:
    app = _app()
    device = EmulatorDeviceInfo("1", "实例", 16448, True)
    # 必须注入假 OcrService：默认 ocr_service=None 会让对话框构造真实服务，
    # show+切识别页路径在 CI（Linux 无头 + xdist）上曾把 worker 阻塞至超时强杀
    dialog = _dialog(
        {"emulator_adb_path": "adb.exe", "emulator_adb_port": 16448, "mumu_ocr_poll_mode": True},
        [device],
        ocr_service=_OcrService(tmp_path),
    )
    dialog.resize(760, 620)
    dialog.show()
    dialog._page_nav.setCurrentRow(1)
    app.processEvents()

    recognition_scroll = dialog._page_stack.currentWidget()
    assert recognition_scroll.horizontalScrollBar().maximum() == 0
    assert dialog._recognition_page.template_section._compact_layout is True
    assert dialog._recognition_page.resume_poll_button.isHidden()

    dialog.resize(900, 680)
    app.processEvents()
    assert recognition_scroll.horizontalScrollBar().maximum() == 0
    assert dialog._recognition_page.template_section._compact_layout is False
    dialog.hide()


def test_resume_poll_action_only_appears_while_paused(tmp_path: Path) -> None:
    _app()
    ocr_service = _OcrService(tmp_path)
    ocr_service.poll_state = "paused"
    dialog = _dialog(
        {"emulator_adb_path": "adb.exe", "emulator_adb_port": 0, "mumu_ocr_poll_mode": True},
        [],
        ocr_service,
    )

    assert not dialog._recognition_page.resume_poll_button.isHidden()
    assert dialog._recognition_page.resume_poll_button.isEnabled()

    dialog._recognition_page.poll_mode_check.setChecked(False)
    assert dialog._recognition_page.resume_poll_button.isHidden()


def test_roi_layout_image_rejects_invalid_file(tmp_path: Path, monkeypatch) -> None:
    _app()
    invalid_path = tmp_path / "invalid.png"
    invalid_path.write_bytes(b"not an image")
    dialog = _dialog({"emulator_adb_path": "adb.exe", "emulator_adb_port": 0}, [])
    warnings: list[str] = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(invalid_path), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))
    monkeypatch.setattr(dialog._recognition_page, "_open_roi_layout_editor", lambda *_args: pytest.fail("不应打开编辑器"))

    dialog._recognition_page.select_roi_layout_image("hero_selection")

    assert warnings and "无法读取图片" in warnings[0]


def test_two_template_types_are_saved_through_ocr_service(tmp_path: Path, monkeypatch) -> None:
    _app()
    ocr_service = _OcrService(tmp_path)
    dialog = _dialog({"emulator_adb_path": "adb.exe", "emulator_adb_port": 0}, [], ocr_service)

    class _AcceptDialog:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def get_roi(self):
            return 10, 10, 30, 20

    monkeypatch.setattr("src.ui.configuration.roi_selector.RoiSelectorDialog", _AcceptDialog)
    image = Image.new("RGB", (80, 60))
    dialog._recognition_page._on_template_screenshot_ready("hero_selection", image)
    dialog._recognition_page._on_template_screenshot_ready("match_guide", image)

    assert ocr_service.created == [
        ("hero_selection", (10, 10, 30, 20)),
        ("match_guide", (10, 10, 30, 20)),
    ]
    assert "模板已保存到" in dialog._shared_toast_overlay.text()


def test_brand_combo_loads_configured_type_and_switch_triggers_refresh() -> None:
    """品牌下拉：装载配置品牌；用户切换后更新草稿并按新品牌刷新设备。"""
    _app()
    dialog = _dialog({"emulator_type": "mumu", "emulator_adb_path": "adb.exe", "emulator_adb_port": 0}, [])
    operation = dialog._operation_service
    page = dialog._device_page

    assert page.brand_combo.count() == 5  # 五家品牌
    assert page.brand_combo.currentData() == "mumu"
    assert operation.refresh_types == ["mumu"]  # 初载按 mumu 刷新

    ld_index = page.brand_combo.findData("ldplayer")
    assert page.brand_combo.itemText(ld_index) == "雷电"
    page.brand_combo.setCurrentIndex(ld_index)
    page.section.brand_activated.emit("ldplayer")  # 用户激活（非程序装载）

    assert dialog._coordinator.emulator_type == "ldplayer"
    assert operation.refresh_types[-1] == "ldplayer"
    assert operation.reprobe_types == ["ldplayer"]  # R3：切换品牌后台静默重探 ADB


class _RefreshFailsAfterSwitchService(_OperationService):
    """初始刷新正常回放设备；置 fail_after_switch=True 后探测必定失败。"""

    def __init__(self, devices: list[EmulatorDeviceInfo]) -> None:
        super().__init__(devices)
        self.fail_after_switch = False

    def refresh_devices(self, emulator_type: str = "mumu") -> None:
        self.refresh_types.append(emulator_type)
        if self.fail_after_switch:
            self.device_refresh_failed.emit("未找到蓝叠安装目录（注册表 BlueStacks_nxt / BlueStacks_nxt_cn）")
        else:
            self.devices_refreshed.emit(self._devices)


def test_brand_switch_clears_previous_brand_results_and_reports_failure() -> None:
    """R4：跨品牌切换清空旧品牌列表，探测失败显性报未探测到 + 原因。"""
    _app()
    device = EmulatorDeviceInfo("1", "雷电 实例 1（端口扫描·品牌未验证）", 5555, True)
    service = _RefreshFailsAfterSwitchService([device])
    dialog = MumuConfigDialog(
        {**_FULL_MUMU_CONFIG, "emulator_adb_path": "adb.exe", "emulator_adb_port": 0},
        capture_service=CaptureService(),
        ocr_service=OcrService(),
        operation_service=service,
    )
    page = dialog._device_page
    assert page.device_combo.currentData() == device  # 旧品牌（雷电）结果在场

    service.fail_after_switch = True
    bs_index = page.brand_combo.findData("bluestacks")
    page.brand_combo.setCurrentIndex(bs_index)
    page.section.brand_activated.emit("bluestacks")

    assert dialog._coordinator.emulator_type == "bluestacks"
    assert dialog._coordinator.devices == ()
    assert page.device_combo.currentData() is None
    assert "未探测到" in page.device_combo.currentText()
    assert "未探测到" in page.instance_status_label.text()
    assert "未找到蓝叠安装目录" in page.instance_status_label.toolTip()
    assert page.port_label.text() == "(自动探测)"


def test_adb_reprobe_result_updates_path_display_silently() -> None:
    """R3：静默重探结果回填路径显示；未找到（空路径）保留旧值。"""
    _app()
    dialog = _dialog({"emulator_adb_path": "old/adb.exe", "emulator_adb_port": 0}, [])
    page = dialog._device_page

    dialog._coordinator.adb_path_reprobed.emit("D:/new/HD-Adb.exe")

    assert page.adb_path_edit.property("raw_path") == "D:/new/HD-Adb.exe"
    assert page.adb_path_edit.text() == "D:/new/HD-Adb.exe"

    dialog._coordinator.adb_path_reprobed.emit("")

    assert page.adb_path_edit.property("raw_path") == "D:/new/HD-Adb.exe"


def test_brand_hint_icon_follows_selected_brand() -> None:
    """R6：品牌特性提示随所选品牌联动——仅 ⓘ 图标，悬停/点击显示说明文案。"""
    _app()
    dialog = _dialog({"emulator_type": "bluestacks", "emulator_adb_path": "adb.exe", "emulator_adb_port": 0}, [])
    section = dialog._device_page.section

    assert not hasattr(section, "brand_hint_label")  # 无常显文案，仅悬停形态
    assert "设置 → 高级" in section.brand_hint_icon.toolTip()
    assert not section.brand_hint_icon.isHidden()

    page = dialog._device_page
    page.brand_combo.setCurrentIndex(page.brand_combo.findData("ldplayer"))

    assert "稳定版雷电 9" in section.brand_hint_icon.toolTip()

    # 悬停（进入）即显示说明，不依赖平台驻留计时器（真机实测该路径不出 tooltip）
    QApplication.sendEvent(section.brand_hint_icon, QEvent(QEvent.Type.Enter))
    assert "稳定版雷电 9" in QToolTip.text()
    # 点击同样显示（离屏下 Leave 淡出需事件循环，隐藏行为不作离屏断言）
    QTest.mouseClick(section.brand_hint_icon, Qt.MouseButton.LeftButton)
    assert "稳定版雷电 9" in QToolTip.text()
