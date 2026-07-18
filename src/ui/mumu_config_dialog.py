"""
名将杀 Agent - 模拟器配置对话框

提供完整的模拟器（MuMu）ADB 连接管理和 OCR 模板配置。
位于 配置 → 模拟器配置 菜单入口。

功能：
  1. 连接管理 — 自动探测 ADB 路径和端口，多设备下拉切换，一键连接/断开，状态监控
  2. 模板管理 — 制作模板（截图+框选），选择模板，打开模板目录，状态显示
  3. OCR 配置 — 启用开关，匹配阈值
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QSignalBlocker, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from src.capture.adb_screen import AdbCapture
from src.capture.image_utils import pil_to_qpixmap
from src.capture.prober import probe_all_devices, probe_mumu_adb, test_adb_path, MuMuDeviceInfo

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_TEMPLATE_DIR = PROJECT_ROOT / "templates"


class MumuConfigDialog(QDialog):
    """模拟器配置对话框"""

    def __init__(self, config: dict, capture_service=None, ocr_service=None, parent=None):
        super().__init__(parent)
        self._config = dict(config)
        self._capture_service = capture_service
        self._ocr_service = ocr_service
        self._capture: AdbCapture | None = capture_service.capture if capture_service else None
        self._devices: list[MuMuDeviceInfo] = []
        self._device_selected_explicitly = False
        self._template_path: str | None = None
        self._roi: tuple[int, int, int, int] | None = None
        self._screenshot_pixmap: QPixmap | None = None

        self.setWindowTitle("模拟器配置")
        self.setMinimumWidth(520)
        self.setMinimumHeight(480)
        self._setup_ui()
        if self._capture_service:
            self._capture_service.connection_changed.connect(self._on_connection_changed)
        self._load_config()

    def _setup_ui(self) -> None:
        """构建对话框界面"""
        layout = QVBoxLayout(self)

        # ════════════════════════════════════════════════
        # 1. ADB 连接管理
        # ════════════════════════════════════════════════
        adb_title = QLabel("ADB 连接管理")
        adb_title.setStyleSheet("font-weight: bold; font-size: 14px; color: #2c3e50;")
        layout.addWidget(adb_title)

        # ADB 路径
        path_row = QHBoxLayout()
        self._adb_path_edit = QLabel()
        self._adb_path_edit.setStyleSheet(
            "border: 1px solid #ccc; padding: 4px 8px; background-color: #f9f9f9; border-radius: 3px;"
        )
        path_row.addWidget(self._adb_path_edit, 1)

        detect_btn = QPushButton("自动探测")
        detect_btn.clicked.connect(self._on_auto_detect)
        path_row.addWidget(detect_btn)

        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_adb)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        # 设备下拉 + 连接控制
        device_row = QHBoxLayout()

        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(200)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        self._device_combo.activated.connect(self._on_device_activated)
        device_row.addWidget(QLabel("设备:"))
        device_row.addWidget(self._device_combo, 1)

        self._connect_btn = QPushButton("连接")
        self._connect_btn.setStyleSheet(
            "padding: 4px 16px; font-weight: bold;"
        )
        self._connect_btn.clicked.connect(self._on_connect_toggle)
        device_row.addWidget(self._connect_btn)

        self._test_device_btn = QPushButton("测试所选设备")
        self._test_device_btn.clicked.connect(self._on_test_selected_device)
        device_row.addWidget(self._test_device_btn)

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._on_refresh_devices)
        device_row.addWidget(refresh_btn)

        layout.addLayout(device_row)

        # 会话状态
        self._instance_status_label = QLabel("实例状态: 未探测")
        self._instance_status_label.setStyleSheet("color: #888; font-size: 12px; padding: 2px 0;")
        layout.addWidget(self._instance_status_label)
        self._status_label = QLabel("ADB 状态: 未配置")
        self._status_label.setStyleSheet("color: #888; font-size: 12px; padding: 2px 0;")
        layout.addWidget(self._status_label)

        # 端口显示
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("ADB 端口:"))
        self._port_label = QLabel("(自动探测)")
        self._port_label.setStyleSheet("color: #555;")
        port_row.addWidget(self._port_label)
        port_row.addStretch()
        layout.addLayout(port_row)

        # ── 分隔线 ────────────────────────────────────
        sep1 = QLabel("─" * 50)
        sep1.setStyleSheet("color: #ccc;")
        layout.addWidget(sep1)

        # ════════════════════════════════════════════════
        # 2. 模板管理
        # ════════════════════════════════════════════════
        template_title = QLabel("识别模板")
        template_title.setStyleSheet("font-weight: bold; font-size: 14px; color: #2c3e50;")
        layout.addWidget(template_title)

        # 模板状态行（无打开文件夹按钮）
        template_status_row = QHBoxLayout()
        self._template_status_icon = QLabel("○")
        self._template_status_icon.setStyleSheet("color: #888; font-size: 16px;")
        template_status_row.addWidget(self._template_status_icon)

        self._template_status_label = QLabel("未设定")
        self._template_status_label.setStyleSheet("color: #888; font-size: 13px;")
        template_status_row.addWidget(self._template_status_label, 1)

        layout.addLayout(template_status_row)

        # 模板操作按钮行
        template_btn_row = QHBoxLayout()

        self._make_template_btn = QPushButton("🎯 制作模板")
        self._make_template_btn.clicked.connect(self._on_make_template)
        template_btn_row.addWidget(self._make_template_btn)

        self._resume_poll_btn = QPushButton("恢复轮询")
        self._resume_poll_btn.clicked.connect(self._on_resume_poll)
        template_btn_row.addWidget(self._resume_poll_btn)

        self._select_template_btn = QPushButton("📁 选择模板")
        self._select_template_btn.clicked.connect(self._on_select_template)
        template_btn_row.addWidget(self._select_template_btn)

        template_btn_row.addStretch()
        layout.addLayout(template_btn_row)

        # ── 分隔线 ────────────────────────────────────
        sep2 = QLabel("─" * 50)
        sep2.setStyleSheet("color: #ccc;")
        layout.addWidget(sep2)

        # ════════════════════════════════════════════════
        # 3. OCR 配置
        # ════════════════════════════════════════════════
        ocr_title = QLabel("武将识别设置")
        ocr_title.setStyleSheet("font-weight: bold; font-size: 14px; color: #2c3e50;")
        layout.addWidget(ocr_title)

        ocr_form = QFormLayout()
        ocr_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._ocr_enabled_check = QCheckBox("启用武将识别（截图后自动 OCR）")
        ocr_form.addRow("", self._ocr_enabled_check)

        self._poll_mode_check = QCheckBox("持续轮询（独立运行，定时检测武将页面）")
        ocr_form.addRow("", self._poll_mode_check)

        poll_interval_row = QHBoxLayout()
        self._poll_interval_spin = QSpinBox()
        self._poll_interval_spin.setRange(1, 60)
        self._poll_interval_spin.setValue(2)
        self._poll_interval_spin.setSuffix(" 秒")
        poll_interval_row.addWidget(self._poll_interval_spin)
        poll_interval_row.addWidget(QLabel("检测间隔"))
        poll_interval_row.addStretch()
        ocr_form.addRow("轮询:", poll_interval_row)

        self._threshold_spin = QDoubleSpinBox()
        self._threshold_spin.setRange(0.1, 1.0)
        self._threshold_spin.setSingleStep(0.05)
        self._threshold_spin.setValue(0.8)
        ocr_form.addRow("匹配阈值:", self._threshold_spin)

        layout.addLayout(ocr_form)
        layout.addStretch()

        # ── 按钮 ──────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ────────────────────────────────────────────────
    # 加载配置
    # ────────────────────────────────────────────────

    def _load_config(self) -> None:
        """从配置加载当前值"""
        adb_path = self._config.get("mumu_adb_path", "")
        if not adb_path:
            detected = probe_mumu_adb()
            if detected:
                adb_path = detected

        self._adb_path_edit.setText(adb_path or "(未设置，点击「自动探测」)")
        self._adb_path_edit.setProperty("raw_path", adb_path)

        adb_port = self._config.get("mumu_adb_port", 0)
        self._port_label.setText(str(adb_port) if adb_port else "(自动探测)")

        self._ocr_enabled_check.setChecked(self._config.get("mumu_ocr_enabled", False))
        self._poll_mode_check.setChecked(self._config.get("mumu_ocr_poll_mode", False))
        self._poll_interval_spin.setValue(self._config.get("mumu_ocr_poll_interval", 2))
        self._threshold_spin.setValue(self._config.get("mumu_ocr_match_threshold", 0.8))

        # 创建 AdbCapture 实例（复用已有的连接）
        if self._capture_service and self._capture_service.capture:
            self._capture = self._capture_service.capture
        elif adb_path:
            self._capture = AdbCapture(adb_path=adb_path, adb_port=adb_port)

        # 刷新设备列表
        self._on_refresh_devices()

        # 检查模板状态
        self._refresh_template_status()
        self._update_ui()

    def _refresh_template_status(self) -> None:
        """更新模板状态显示"""
        from src.ocr.ocr_loader import get_template_manager
        tm = get_template_manager()
        if tm.is_loaded:
            self._template_status_icon.setText("●")
            self._template_status_icon.setStyleSheet("color: #27ae60; font-size: 16px;")
            self._template_status_label.setText(f"已加载: {tm.template_path.name}")
            self._template_status_label.setStyleSheet("color: #27ae60; font-size: 13px;")
        else:
            self._template_status_icon.setText("○")
            self._template_status_icon.setStyleSheet("color: #888; font-size: 16px;")
            self._template_status_label.setText("未设定")
            self._template_status_label.setStyleSheet("color: #888; font-size: 13px;")

    # ────────────────────────────────────────────────
    # ADB 连接管理
    # ────────────────────────────────────────────────

    def _on_auto_detect(self) -> None:
        """自动探测 ADB 路径和端口"""
        logger.info("开始自动探测 ADB...")

        # 探测 adb 路径
        adb_path = probe_mumu_adb()
        if adb_path:
            ok, msg = test_adb_path(adb_path)
            if ok:
                self._adb_path_edit.setText(adb_path)
                self._adb_path_edit.setProperty("raw_path", adb_path)
                self._adb_path_edit.setStyleSheet(
                    "border: 1px solid #27ae60; padding: 4px 8px; background-color: #f0faf0; border-radius: 3px;"
                )

                # 让共享服务使用当前草稿配置，连接状态由它统一发布
                self._sync_capture_service_config()
                self._on_refresh_devices()

                QMessageBox.information(self, "自动探测", f"找到 ADB:\n{adb_path}\n{msg}")
            else:
                self._adb_path_edit.setStyleSheet(
                    "border: 1px solid #e74c3c; padding: 4px 8px; background-color: #fdf0ef; border-radius: 3px;"
                )
                QMessageBox.warning(self, "自动探测", f"ADB 文件存在但验证失败:\n{msg}")
        else:
            self._adb_path_edit.setStyleSheet(
                "border: 1px solid #e74c3c; padding: 4px 8px; background-color: #fdf0ef; border-radius: 3px;"
            )
            QMessageBox.warning(self, "自动探测", "未找到 ADB，请手动设置路径")

    def _browse_adb(self) -> None:
        """弹出文件选择对话框选择 adb.exe"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 ADB 可执行文件", "",
            "adb (*.exe);;所有文件 (*.*)"
        )
        if path:
            self._adb_path_edit.setText(path)
            self._adb_path_edit.setProperty("raw_path", path)
            self._adb_path_edit.setStyleSheet(
                "border: 1px solid #ccc; padding: 4px 8px; background-color: #f9f9f9; border-radius: 3px;"
            )
            self._sync_capture_service_config()
            self._on_refresh_devices()

    def _sync_capture_service_config(self) -> None:
        """将当前编辑中的 ADB 配置同步到共享截图服务。"""
        if not self._capture_service:
            return
        if self._capture_service:
            config = dict(self._config)
            config["mumu_adb_path"] = self._adb_path_edit.property("raw_path") or ""
            self._capture_service.update_config(config)
            self._capture = self._capture_service.capture
        else:
            self._capture = AdbCapture(
                adb_path=self._adb_path_edit.property("raw_path") or "",
                adb_port=self._config.get("mumu_adb_port", 0),
            )

    def _on_refresh_devices(self) -> None:
        """刷新设备列表，并避免在多实例时擅自选择目标。"""
        configured_port = self._config.get("mumu_adb_port", 0)
        self._devices = probe_all_devices()
        running_devices = [device for device in self._devices if device.is_running and device.adb_port]
        self._device_selected_explicitly = False

        with QSignalBlocker(self._device_combo):
            self._device_combo.clear()
            if not self._devices:
                self._device_combo.addItem("(未探测到设备)")
                self._device_combo.setEnabled(False)
                self._instance_status_label.setText("实例状态: 未探测到")
                self._port_label.setText("(自动探测)")
                self._update_ui()
                return

            self._device_combo.setEnabled(True)
            for device in self._devices:
                running_text = "运行中" if device.is_running else "未运行"
                label = f"[{device.index}] {device.name}（{running_text}）"
                if device.adb_port:
                    label += f"  (端口:{device.adb_port})"
                self._device_combo.addItem(label, userData=device)

            target_index = next(
                (
                    index for index in range(self._device_combo.count())
                    if (device := self._device_combo.itemData(index)) and device.adb_port == configured_port
                ),
                -1,
            ) if configured_port else -1

            if target_index >= 0:
                self._device_combo.setCurrentIndex(target_index)
            elif configured_port == 0 and len(running_devices) == 1:
                target_index = next(
                    index for index in range(self._device_combo.count())
                    if self._device_combo.itemData(index) is running_devices[0]
                )
                self._device_combo.setCurrentIndex(target_index)
            elif configured_port == 0 and len(running_devices) > 1:
                self._device_combo.insertItem(0, "请选择运行中的实例", userData=None)
                self._device_combo.setCurrentIndex(0)
            else:
                self._device_combo.setCurrentIndex(0)

        self._on_device_changed(self._device_combo.currentIndex())
        self._update_ui()

    def _on_device_activated(self, index: int) -> None:
        """记录用户对实例的显式选择。"""
        if self._device_combo.itemData(index):
            self._device_selected_explicitly = True

    def _on_device_changed(self, index: int) -> None:
        """设备下拉选择变化"""
        if index < 0 or not self._devices:
            return

        device = self._device_combo.itemData(index)
        if device and device.adb_port:
            self._port_label.setText(str(device.adb_port))
            state = "运行中" if device.is_running else "未运行"
            self._instance_status_label.setText(f"实例状态: {state}")
        else:
            self._port_label.setText("(自动探测)")
            self._instance_status_label.setText("实例状态: 未探测到")
        self._update_ui()

    def _on_connection_changed(self, _state: str, _detail: str) -> None:
        """共享 CaptureService 会话变化时刷新配置页。"""
        self._capture = self._capture_service.capture if self._capture_service else None
        self._update_ui()

    def _on_connect_toggle(self) -> None:
        """连接/断开切换"""
        if self._capture and self._capture.connected:
            self._disconnect_emulator()
        else:
            self._connect_emulator()

    def _connect_emulator(self) -> None:
        """连接选中的模拟器。"""
        if not self._capture_service:
            QMessageBox.warning(self, "连接失败", "截图服务不可用")
            return
        device = self._device_combo.currentData()
        running_devices = [item for item in self._devices if item.is_running and item.adb_port]
        if self._config.get("mumu_adb_port", 0) == 0 and len(running_devices) > 1 and not self._device_selected_explicitly:
            QMessageBox.warning(self, "请选择设备", "检测到多个运行中的 MuMu 实例，请先选择要连接的实例。")
            return
        if self._device_selected_explicitly and device and device.adb_port:
            self._capture_service.set_target_port(device.adb_port)
            self._capture = self._capture_service.capture

        self._connect_btn.setEnabled(False)
        QTimer.singleShot(50, self._do_connect)

    def _do_connect(self) -> None:
        """实际执行连接。"""
        if not self._capture_service:
            self._connect_btn.setEnabled(True)
            return
        ok, message = self._capture_service.connect_emulator()
        if not ok:
            QMessageBox.warning(self, "连接失败", message)
        self._connect_btn.setEnabled(True)
        self._update_ui()

    def _disconnect_emulator(self) -> None:
        """断开模拟器。"""
        if self._capture_service:
            self._capture_service.disconnect_emulator()
        self._update_ui()

    def _on_test_selected_device(self) -> None:
        """测试当前选择的设备连通性，不改变共享会话或配置。"""
        adb_path = self._adb_path_edit.property("raw_path") or ""
        device = self._device_combo.currentData()
        if not adb_path:
            QMessageBox.warning(self, "设备测试", "请先配置 ADB 路径。")
            return
        if not device or not device.adb_port:
            QMessageBox.warning(self, "设备测试", "请选择一个带有效 ADB 端口的实例后再测试。")
            return

        target = f"127.0.0.1:{device.adb_port}"
        self._test_device_btn.setEnabled(False)
        self._test_device_btn.setText("测试中...")
        QTimer.singleShot(0, lambda: self._do_test_selected_device(adb_path, device.adb_port, target))

    def _do_test_selected_device(self, adb_path: str, port: int, target: str) -> None:
        """执行精确目标的 connect + get-state 测试。"""
        try:
            shared = self._capture_service.capture if self._capture_service else None
            if shared and shared.connected and shared.device_serial == target:
                capture = shared
            else:
                capture = AdbCapture(adb_path=adb_path, adb_port=port)
            ok, message = capture.connect()
            if ok:
                ok, state = capture.check_device()
                message = state if ok else state
            if ok:
                QMessageBox.information(self, "设备测试成功", f"已连接到所选设备：\n{target}\n设备状态：device")
            else:
                QMessageBox.warning(self, "设备测试失败", f"无法连接所选设备 {target}：\n{message}")
        finally:
            self._test_device_btn.setEnabled(True)
            self._test_device_btn.setText("测试所选设备")
            self._update_ui()

    def _on_resume_poll(self) -> None:
        """恢复已暂停的 OCR 轮询。"""
        if self._ocr_service and self._ocr_service.poll_state == "paused":
            self._ocr_service.resume_poll()
            self._update_ui()


    def _on_make_template(self) -> None:
        """制作模板：截图 → 框选 → 保存"""
        if not self._capture or not self._capture.connected:
            # 尝试先连接
            if self._capture:
                ok, msg = self._capture.connect()
                if not ok:
                    QMessageBox.warning(self, "制作模板", f"请先连接模拟器\n{msg}")
                    return
            else:
                QMessageBox.warning(self, "制作模板", "请先配置 ADB 并连接模拟器")
                return

        self._make_template_btn.setEnabled(False)
        self._make_template_btn.setText("正在截图...")
        QTimer.singleShot(50, self._do_make_template)

    def _do_make_template(self) -> None:
        """实际执行模板制作"""
        try:
            ok, result = self._capture.screencap_full()
            if not ok:
                self._make_template_btn.setEnabled(True)
                self._make_template_btn.setText("🎯 制作模板")
                QMessageBox.warning(self, "制作模板", f"截图失败:\n{result}")
                return

            image = result

            # 转为 QPixmap 显示
            pixmap = pil_to_qpixmap(image)
            if pixmap.isNull():
                self._make_template_btn.setEnabled(True)
                self._make_template_btn.setText("🎯 制作模板")
                QMessageBox.warning(self, "制作模板", "图像转换失败")
                return

            # 打开框选对话框
            from src.ui.roi_selector import RoiSelectorDialog
            dialog = RoiSelectorDialog(pixmap, title="框选模板区域（如页面标题或按钮）", parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self._make_template_btn.setEnabled(True)
                self._make_template_btn.setText("🎯 制作模板")
                return

            roi = dialog.get_roi()
            if not roi:
                self._make_template_btn.setEnabled(True)
                self._make_template_btn.setText("🎯 制作模板")
                return

            # 保存模板
            from src.ocr.ocr_loader import get_template_manager
            tm = get_template_manager()
            tm.set_template(image, roi)

            self._refresh_template_status()
            QMessageBox.information(
                self, "模板已保存",
                f"模板已保存到:\n{tm.template_path}\n\n"
                f"ROI: ({roi[0]}, {roi[1]})  {roi[2]}×{roi[3]}"
            )

        except Exception as e:
            logger.error("制作模板异常: %s", e)
            logger.debug(traceback.format_exc())
            QMessageBox.warning(self, "制作模板", f"制作模板时出错:\n{e}")

        self._make_template_btn.setEnabled(True)
        self._make_template_btn.setText("🎯 制作模板")

    def _on_select_template(self) -> None:
        """选择模板文件"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模板图片", str(DEFAULT_TEMPLATE_DIR),
            "图片 (*.png *.jpg *.jpeg)"
        )
        if path:
            from src.ocr.ocr_loader import get_template_manager
            import shutil

            tm = get_template_manager()
            tm.template_path.parent.mkdir(parents=True, exist_ok=True)

            src = Path(path)
            dst = tm.template_path
            if src.resolve() != dst.resolve():
                shutil.copy2(str(src), str(dst))
                # 外部模板通常没有本项目的参考尺寸元数据，不能沿用旧模板的尺寸。
                metadata_path = dst.with_suffix(".json")
                if metadata_path.exists():
                    try:
                        metadata_path.unlink()
                    except OSError as exc:
                        logger.warning("旧模板元数据清理失败: %s", exc)

            tm.reload()
            self._template_path = str(dst)
            self._refresh_template_status()



    # ────────────────────────────────────────────────
    # 连接测试
    # ────────────────────────────────────────────────

    def _test_connection(self) -> None:
        """测试 ADB 连接"""
        adb_path = self._adb_path_edit.property("raw_path") or ""
        if adb_path:
            ok, msg = test_adb_path(adb_path)
            if ok:
                QMessageBox.information(self, "连接测试", f"ADB 验证成功\n{msg}")
            else:
                QMessageBox.warning(self, "连接测试", f"ADB 验证失败\n{msg}")
        else:
            QMessageBox.warning(self, "连接测试", "请先配置 ADB 路径")

    # ────────────────────────────────────────────────
    # UI 更新
    # ────────────────────────────────────────────────

    def _update_ui(self) -> None:
        """根据实例运行状态与真实 ADB 会话状态更新控件。"""
        if self._capture_service:
            state, detail = self._capture_service.connection_state
        elif self._capture:
            state, detail = ("connected", self._capture.device_serial) if self._capture.connected else ("disconnected", "")
        else:
            state, detail = "unconfigured", ""

        states = {
            "unconfigured": ("ADB 状态: 未配置", "#888"),
            "disconnected": ("ADB 状态: 未连接", "#888"),
            "connecting": ("ADB 状态: 连接中...", "#f39c12"),
            "connected": (f"ADB 状态: 已连接 ({detail})", "#27ae60"),
            "offline": ("ADB 状态: 设备离线", "#e74c3c"),
        }
        text, color = states.get(state, states["disconnected"])
        self._status_label.setText(text)
        self._status_label.setToolTip(detail)
        self._status_label.setStyleSheet(f"color: {color}; font-size: 12px; padding: 2px 0;")
        self._connect_btn.setText("断开" if state == "connected" else "连接")
        self._connect_btn.setEnabled(state != "connecting")
        self._make_template_btn.setEnabled(state == "connected")
        self._resume_poll_btn.setVisible(self._ocr_service is not None)
        self._resume_poll_btn.setEnabled(
            self._ocr_service is not None and self._ocr_service.poll_state == "paused"
        )
        self._test_device_btn.setEnabled(self._device_combo.currentData() is not None)

    # ────────────────────────────────────────────────
    # 保存
    # ────────────────────────────────────────────────

    def _on_save(self) -> None:
        """保存配置"""
        try:
            raw_path = self._adb_path_edit.property("raw_path") or ""
            self._config["mumu_adb_path"] = raw_path

            configured_port = self._config.get("mumu_adb_port", 0)
            device = self._device_combo.currentData()
            running_devices = [item for item in self._devices if item.is_running and item.adb_port]
            if configured_port == 0 and len(running_devices) > 1 and not self._device_selected_explicitly:
                QMessageBox.warning(self, "请选择设备", "检测到多个运行中的 MuMu 实例，请选择要使用的实例后再保存。")
                return
            if self._device_selected_explicitly and device and device.adb_port:
                self._config["mumu_adb_port"] = device.adb_port
            elif configured_port == 0:
                self._config["mumu_adb_port"] = 0
            elif device and device.adb_port:
                self._config["mumu_adb_port"] = device.adb_port

            self._config["mumu_ocr_enabled"] = self._ocr_enabled_check.isChecked()
            self._config["mumu_ocr_poll_mode"] = self._poll_mode_check.isChecked()
            self._config["mumu_ocr_poll_interval"] = self._poll_interval_spin.value()
            self._config["mumu_ocr_match_threshold"] = round(self._threshold_spin.value(), 2)
            self.accept()
        except Exception as e:
            logger.exception("保存模拟器配置失败")
            QMessageBox.critical(self, "保存失败", f"保存配置时出错:\n{e}")

    def get_config(self) -> dict:
        """获取用户修改后的配置"""
        return self._config

    def get_connected(self) -> bool:
        """是否已连接"""
        return self._capture.connected if self._capture else False
