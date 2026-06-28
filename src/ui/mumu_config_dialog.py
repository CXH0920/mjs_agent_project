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

from PySide6.QtCore import Qt, QTimer
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

    def __init__(self, config: dict, capture_service=None, parent=None):
        super().__init__(parent)
        self._config = dict(config)
        self._capture_service = capture_service
        self._capture: AdbCapture | None = capture_service.capture if capture_service else None
        self._devices: list[MuMuDeviceInfo] = []
        self._template_path: str | None = None
        self._roi: tuple[int, int, int, int] | None = None
        self._screenshot_pixmap: QPixmap | None = None

        self.setWindowTitle("模拟器配置")
        self.setMinimumWidth(520)
        self.setMinimumHeight(480)
        self._setup_ui()
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
            "border: 1px solid #ccc; padding: 4px 8px; background: #f9f9f9; border-radius: 3px;"
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
        device_row.addWidget(QLabel("设备:"))
        device_row.addWidget(self._device_combo, 1)

        self._connect_btn = QPushButton("连接")
        self._connect_btn.setStyleSheet(
            "padding: 4px 16px; font-weight: bold;"
        )
        self._connect_btn.clicked.connect(self._on_connect_toggle)
        device_row.addWidget(self._connect_btn)

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._on_refresh_devices)
        device_row.addWidget(refresh_btn)

        layout.addLayout(device_row)

        # 连接状态
        self._status_label = QLabel("状态: 未连接")
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
                    "border: 1px solid #27ae60; padding: 4px 8px; background: #f0faf0; border-radius: 3px;"
                )

                # 重建 AdbCapture
                self._capture = AdbCapture(adb_path=adb_path, adb_port=self._config.get("mumu_adb_port", 0))
                self._on_refresh_devices()

                QMessageBox.information(self, "自动探测", f"找到 ADB:\n{adb_path}\n{msg}")
            else:
                self._adb_path_edit.setStyleSheet(
                    "border: 1px solid #e74c3c; padding: 4px 8px; background: #fdf0ef; border-radius: 3px;"
                )
                QMessageBox.warning(self, "自动探测", f"ADB 文件存在但验证失败:\n{msg}")
        else:
            self._adb_path_edit.setStyleSheet(
                "border: 1px solid #e74c3c; padding: 4px 8px; background: #fdf0ef; border-radius: 3px;"
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
                "border: 1px solid #ccc; padding: 4px 8px; background: #f9f9f9; border-radius: 3px;"
            )
            self._capture = AdbCapture(adb_path=path, adb_port=self._config.get("mumu_adb_port", 0))
            self._on_refresh_devices()

    def _on_refresh_devices(self) -> None:
        """刷新设备列表"""
        self._device_combo.clear()
        self._devices = probe_all_devices()

        if not self._devices:
            self._device_combo.addItem("(未探测到设备)")
            self._device_combo.setEnabled(False)
            return

        self._device_combo.setEnabled(True)
        running_count = 0
        for d in self._devices:
            icon = "●" if d.is_running else "○"
            label = f"{icon} [{d.index}] {d.name}"
            if d.adb_port:
                label += f"  (端口:{d.adb_port})"
            self._device_combo.addItem(label, userData=d)
            if d.is_running:
                running_count += 1

        logger.info("设备列表刷新: %d 个实例 (%d 运行中)", len(self._devices), running_count)

        # 自动选择运行中的实例
        if running_count > 0:
            for i in range(self._device_combo.count()):
                d = self._device_combo.itemData(i)
                if d and d.is_running:
                    self._device_combo.setCurrentIndex(i)
                    break

    def _on_device_changed(self, index: int) -> None:
        """设备下拉选择变化"""
        if index < 0 or not self._devices:
            return

        device = self._device_combo.itemData(index)
        if device and device.adb_port:
            self._port_label.setText(str(device.adb_port))
        else:
            self._port_label.setText("(自动探测)")

    def _on_connect_toggle(self) -> None:
        """连接/断开切换"""
        if self._capture and self._capture.connected:
            self._disconnect_emulator()
        else:
            self._connect_emulator()

    def _connect_emulator(self) -> None:
        """连接模拟器"""
        if not self._capture:
            QMessageBox.warning(self, "连接失败", "请先配置 ADB 路径")
            return

        index = self._device_combo.currentIndex()
        if index >= 0 and self._devices:
            device = self._device_combo.itemData(index)
            if device and device.adb_port:
                # 通过服务连接（确保信号连接者的 AdbCapture 也被更新）
                if self._capture_service:
                    new_cap = AdbCapture(
                        adb_path=self._capture._adb_path,
                        adb_port=device.adb_port,
                    )
                    self._capture_service.capture = new_cap
                    self._capture = new_cap

        self._connect_btn.setEnabled(False)
        self._connect_btn.setText("连接中...")
        self._status_label.setText("状态: 连接中...")
        self._status_label.setStyleSheet("color: #f39c12; font-size: 12px; padding: 2px 0;")
        self._update_ui()

        # 使用 QTimer 让 UI 先刷新再执行连接
        QTimer.singleShot(50, self._do_connect)

    def _do_connect(self) -> None:
        """实际执行连接（在 QTimer 回调中）"""
        if not self._capture:
            self._connect_btn.setEnabled(True)
            self._connect_btn.setText("连接")
            return

        ok, msg = self._capture.connect()
        if ok:
            self._status_label.setText(f"状态: 已连接 ({self._capture.device_serial})")
            self._status_label.setStyleSheet("color: #27ae60; font-size: 12px; padding: 2px 0;")
            self._connect_btn.setText("断开")
            # 同步到 capture_service（让截图流程复用此连接）
            if self._capture_service:
                self._capture_service.capture = self._capture
                self._capture_service.update_config(self._capture_service._config)  # 刷新引用
        else:
            self._status_label.setText(f"状态: 连接失败 - {msg}")
            self._status_label.setStyleSheet("color: #e74c3c; font-size: 12px; padding: 2px 0;")
            self._connect_btn.setText("连接")

        self._connect_btn.setEnabled(True)
        self._update_ui()

    def _disconnect_emulator(self) -> None:
        """断开模拟器"""
        if not self._capture:
            return

        self._capture_service.disconnect_emulator()
        self._status_label.setText("状态: 未连接")
        self._status_label.setStyleSheet("color: #888; font-size: 12px; padding: 2px 0;")
        self._connect_btn.setText("连接")
        self._update_ui()

    # ────────────────────────────────────────────────
    # 模板管理
    # ────────────────────────────────────────────────

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
        """根据状态更新 UI 元素"""
        connected = self._capture.connected if self._capture else False
        self._make_template_btn.setEnabled(connected)

    # ────────────────────────────────────────────────
    # 保存
    # ────────────────────────────────────────────────

    def _on_save(self) -> None:
        """保存配置"""
        try:
            raw_path = self._adb_path_edit.property("raw_path") or ""
            self._config["mumu_adb_path"] = raw_path

            index = self._device_combo.currentIndex()
            if index >= 0 and self._devices:
                device = self._device_combo.itemData(index)
                if device and device.adb_port:
                    self._config["mumu_adb_port"] = device.adb_port
            else:
                self._config["mumu_adb_port"] = 0

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
