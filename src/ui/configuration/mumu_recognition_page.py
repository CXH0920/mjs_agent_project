# -*- coding: utf-8 -*-
"""模拟器配置·识别与自动化页（审计 G2 切片 4.1b）。

从 MumuConfigDialog 拆出：模板选择/制作、ROI 识别区域编辑与轮询参数的
信号接线与控件状态收敛到本页；对话框只负责页面装配、页头 ADB 总状态与
保存编排。控件来自 MumuTemplateSection + MumuOcrPollingSection（构建层，
后者已内嵌前者），后台操作全部委托 MumuConfigCoordinator（业务层）。
弹窗与 Toast 以 self.window()（对话框）为父级，保持拆分前的呈现位置与
共享 overlay 语义。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QVBoxLayout, QWidget
from src.business.emulator.mumu_config_coordinator import MumuConfigCoordinator
from src.config.env import BUNDLE_ROOT, SCREENSHOTS_DIR
from src.ui.configuration.mumu_config_sections import (
    MumuOcrPollingSection,
    MumuTemplateSection,
)
from src.ui.shared.image_utils import pil_to_qpixmap
from src.ui.shared.widgets import show_toast

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE_DIR = BUNDLE_ROOT / "templates"
DEFAULT_SCREENSHOTS_DIR = SCREENSHOTS_DIR


class MumuRecognitionPage(QWidget):
    """「识别与自动化」页：模板管理、ROI 识别区域与轮询参数。"""

    ui_refresh_requested = Signal()

    def __init__(self, coordinator: MumuConfigCoordinator, parent=None) -> None:
        super().__init__(parent)
        self._coordinator = coordinator

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.template_section = MumuTemplateSection(self)
        self.ocr_polling_section = MumuOcrPollingSection(self.template_section, self)
        layout.addWidget(self.ocr_polling_section)

        self.template_section.hero_select_requested.connect(self._on_select_template)
        self.template_section.hero_make_requested.connect(self._on_make_template)
        self.template_section.match_guide_select_requested.connect(self._on_select_match_guide_template)
        self.template_section.match_guide_make_requested.connect(self._on_make_match_guide_template)

        self.ocr_polling_section.poll_mode_changed.connect(self._update_parameter_controls)
        self.ocr_polling_section.resume_requested.connect(self._on_resume_poll)
        self.ocr_polling_section.roi_capture_requested.connect(self._start_roi_layout_capture)
        self.ocr_polling_section.roi_image_requested.connect(self.select_roi_layout_image)
        self.ocr_polling_section.roi_reset_requested.connect(self._reset_roi_layout)

        coordinator.template_screenshot_ready.connect(self._on_template_screenshot_ready)
        coordinator.template_screenshot_failed.connect(self._on_template_screenshot_failed)
        coordinator.template_capture_finished.connect(self._restore_template_button)
        coordinator.roi_layout_screenshot_ready.connect(self._on_roi_layout_screenshot_ready)
        coordinator.roi_layout_screenshot_failed.connect(self._on_roi_layout_screenshot_failed)
        coordinator.roi_layout_capture_finished.connect(self._restore_roi_layout_button)

    # 测试锚点别名（对齐拆分前对话框的控件访问）
    @property
    def make_template_button(self):
        return self.template_section.hero_make_button

    @property
    def match_guide_make_button(self):
        return self.template_section.match_guide_make_button

    @property
    def resume_poll_button(self):
        return self.ocr_polling_section.resume_button

    @property
    def poll_mode_check(self):
        return self.ocr_polling_section.poll_mode_check

    @property
    def auto_switch_tab_check(self):
        return self.ocr_polling_section.auto_switch_tab_check

    @property
    def ocr_enabled_check(self):
        return self.ocr_polling_section.ocr_enabled_check

    # ────────────────────────────────────────────────
    # 配置装载 / 状态渲染 / 草稿收集（对话框编排入口）
    # ────────────────────────────────────────────────

    def load_config(self, config: dict) -> None:
        """从配置加载识别页控件当前值，并刷新两组模板状态。"""
        polling = self.ocr_polling_section
        polling.ocr_enabled_check.setChecked(config.get("mumu_ocr_enabled", False))
        polling.poll_mode_check.setChecked(config.get("mumu_ocr_poll_mode", False))
        polling.poll_idle_pause_check.setChecked(config.get("mumu_ocr_poll_idle_pause", True))
        polling.auto_switch_tab_check.setChecked(config.get("mumu_ocr_auto_switch_tab", False))
        # 配置出自 get_mumu_config() 全键字典（协调器持有），默认值以 env 层为唯一权威，直接取键
        polling.poll_interval_spin.setValue(config["mumu_ocr_poll_interval"])
        polling.threshold_spin.setValue(config["mumu_hero_selection_threshold"])
        polling.match_guide_threshold_spin.setValue(config["mumu_match_guide_threshold"])
        polling.hero_cooldown_spin.setValue(config["mumu_hero_selection_cooldown"])

        self._refresh_template_status()
        self._refresh_match_guide_template_status()

    def update_state(self, state: str) -> None:
        """按 ADB 会话状态与后台任务进度更新识别页控件。"""
        template = self.template_section
        polling = self.ocr_polling_section
        # 制作模板流程本身会在未连接时尝试建立 ADB 会话，不能只按
        # connected 状态禁用按钮，否则用户无法从模板按钮触发自动连接。
        can_make_template = self._coordinator.capture is not None and state != "connecting"
        template.hero_make_button.setEnabled(
            can_make_template and not self._coordinator.is_template_capture_in_progress("hero_selection")
        )
        template.match_guide_make_button.setEnabled(
            can_make_template and not self._coordinator.is_template_capture_in_progress("match_guide")
        )
        template.hero_select_button.setEnabled(state != "connecting")
        template.match_guide_select_button.setEnabled(state != "connecting")
        can_capture_roi = self._coordinator.capture is not None and state != "connecting"
        polling.hero_roi_capture_button.setEnabled(
            can_capture_roi and not self._coordinator.is_roi_layout_capture_in_progress("hero_selection")
        )
        polling.match_guide_roi_capture_button.setEnabled(
            can_capture_roi and not self._coordinator.is_roi_layout_capture_in_progress("match_guide")
        )
        polling.hero_roi_image_button.setEnabled(state != "connecting")
        polling.match_guide_roi_image_button.setEnabled(state != "connecting")
        polling.hero_roi_reset_button.setEnabled(state != "connecting")
        polling.match_guide_roi_reset_button.setEnabled(state != "connecting")
        self._update_parameter_controls()

    def collect_parameters(self) -> dict:
        """收集识别页表单值（不含 ADB 路径，设备侧归 MumuDevicePage）。"""
        polling = self.ocr_polling_section
        return {
            "mumu_ocr_enabled": polling.ocr_enabled_check.isChecked(),
            "mumu_ocr_poll_mode": polling.poll_mode_check.isChecked(),
            "mumu_ocr_poll_idle_pause": polling.poll_idle_pause_check.isChecked(),
            "mumu_ocr_auto_switch_tab": polling.auto_switch_tab_check.isChecked(),
            "mumu_ocr_poll_interval": polling.poll_interval_spin.value(),
            "mumu_ocr_match_threshold": round(polling.threshold_spin.value(), 2),
            "mumu_hero_selection_threshold": round(polling.threshold_spin.value(), 2),
            "mumu_match_guide_threshold": round(polling.match_guide_threshold_spin.value(), 2),
            "mumu_hero_selection_cooldown": polling.hero_cooldown_spin.value(),
        }

    # ────────────────────────────────────────────────
    # 模板状态
    # ────────────────────────────────────────────────

    def _refresh_template_status(self) -> None:
        """更新模板状态显示"""
        self._apply_template_status(
            self.template_section.hero_status_icon,
            self.template_section.hero_status_label,
            self._coordinator.template_status(),
        )

    def _refresh_match_guide_template_status(self) -> None:
        """更新对局攻略模板状态显示。"""
        self._apply_template_status(
            self.template_section.match_guide_status_icon,
            self.template_section.match_guide_status_label,
            self._coordinator.template_status("match_guide"),
        )

    @staticmethod
    def _apply_template_status(icon, label, status) -> None:
        """把一份模板状态渲染到指定的图标/标签控件对（两组控件共用）。"""
        if status.loaded:
            icon.setText("●")
            icon.setStyleSheet("color: #27ae60; font-size: 16px;")
            label.setText(f"已加载：{status.path.name}")
            label.setToolTip(str(status.path))
            label.setStyleSheet("color: #27ae60; font-size: 13px;")
        else:
            icon.setText("○")
            icon.setStyleSheet("color: #888; font-size: 16px;")
            label.setText("未设定")
            label.setToolTip("")
            label.setStyleSheet("color: #888; font-size: 13px;")

    def _refresh_template(self, template_name: str) -> None:
        if template_name == "match_guide":
            self._refresh_match_guide_template_status()
        else:
            self._refresh_template_status()

    # ────────────────────────────────────────────────
    # 模板选择 / 制作
    # ────────────────────────────────────────────────

    def start_template_capture(self, template_name: str) -> None:
        """发起一次模板制作截图（对话框与测试共用的公开入口）。"""
        if not self._coordinator.start_template_capture(template_name):
            return
        button = self._template_button(template_name)
        button.setEnabled(False)
        button.setText("正在截图...")

    def _on_make_template(self) -> None:
        self.start_template_capture("hero_selection")

    def _on_select_template(self) -> None:
        """选择模板文件"""
        path, _ = QFileDialog.getOpenFileName(
            self.window(), "选择模板图片", str(DEFAULT_TEMPLATE_DIR),
            "图片 (*.png *.jpg *.jpeg)"
        )
        if not path:
            return
        try:
            self._coordinator.select_template(path)
            self._refresh_template_status()
        except Exception as exc:
            QMessageBox.warning(self.window(), "选择模板", f"加载模板时出错:\n{exc}")

    def _on_make_match_guide_template(self) -> None:
        self.start_template_capture("match_guide")

    def _on_select_match_guide_template(self) -> None:
        """选择并保存对局攻略模板文件。"""
        path, _ = QFileDialog.getOpenFileName(
            self.window(), "选择对局攻略模板图片", str(DEFAULT_TEMPLATE_DIR),
            "图片 (*.png *.jpg *.jpeg)",
        )
        if not path:
            return
        try:
            self._coordinator.select_template(path, "match_guide")
            self._refresh_match_guide_template_status()
        except Exception as exc:
            QMessageBox.warning(self.window(), "选择对局攻略模板", f"加载模板时出错:\n{exc}")

    def _template_button(self, template_name: str):
        return (
            self.template_section.match_guide_make_button
            if template_name == "match_guide" else self.template_section.hero_make_button
        )

    def _restore_template_button(self, template_name: str) -> None:
        button = self._template_button(template_name)
        button.setEnabled(True)
        button.setText("制作模板")

    def _on_template_screenshot_ready(self, template_name: str, image) -> None:
        try:
            pixmap = pil_to_qpixmap(image)
            if pixmap.isNull():
                QMessageBox.warning(self.window(), "制作模板", "图像转换失败")
                return

            from src.ui.configuration.roi_selector import RoiSelectorDialog

            title = "框选对局攻略页面模板区域" if template_name == "match_guide" else "框选模板区域（如页面标题或按钮）"
            dialog = RoiSelectorDialog(pixmap, title=title, parent=self.window())
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            roi = dialog.get_roi()
            if not roi:
                return

            self._coordinator.create_template(image, roi, template_name)
            self._refresh_template(template_name)
            template_path = self._coordinator.template_status(template_name).path
            message = f"模板已保存到:\n{template_path}"
            if template_name == "hero_selection":
                message += f"\n\nROI: ({roi[0]}, {roi[1]})  {roi[2]}×{roi[3]}"
            show_toast(self.window(), message, duration=3000)
        except Exception as exc:
            logger.exception("制作模板异常")
            QMessageBox.warning(self.window(), "制作模板", f"制作模板时出错:\n{exc}")
        finally:
            self._coordinator.finish_template_capture(template_name)

    def _on_template_screenshot_failed(self, template_name: str, message: str) -> None:
        QMessageBox.warning(self.window(), "制作模板", f"截图失败:\n{message}")

    # ────────────────────────────────────────────────
    # ROI 识别区域
    # ────────────────────────────────────────────────

    def _start_roi_layout_capture(self, page_type: str) -> None:
        if not self._coordinator.start_roi_layout_capture(page_type):
            return
        button = self._roi_layout_capture_button(page_type)
        button.setEnabled(False)
        button.setText("正在截图...")

    def select_roi_layout_image(self, page_type: str) -> None:
        """从本地图片打开 ROI 布局编辑器（对话框与测试共用的公开入口）。"""
        path, _ = QFileDialog.getOpenFileName(
            self.window(),
            "选择用于调整识别区域的截图",
            str(DEFAULT_SCREENSHOTS_DIR),
            "图片 (*.png *.jpg *.jpeg)",
        )
        if not path:
            return
        try:
            image = self._coordinator.load_preview_image(path)
            self._open_roi_layout_editor(page_type, image)
        except Exception as exc:
            logger.exception("读取 OCR ROI 截图失败")
            QMessageBox.warning(self.window(), "编辑识别区域", f"无法读取图片:\n{exc}")

    def _on_roi_layout_screenshot_ready(self, page_type: str, image) -> None:
        try:
            self._open_roi_layout_editor(page_type, image)
        finally:
            self._coordinator.finish_roi_layout_capture(page_type)

    def _on_roi_layout_screenshot_failed(self, _page_type: str, message: str) -> None:
        QMessageBox.warning(self.window(), "编辑识别区域", f"截图失败:\n{message}")

    def _open_roi_layout_editor(self, page_type: str, image) -> None:
        try:
            pixmap = pil_to_qpixmap(image)
            if pixmap.isNull():
                raise ValueError("图像转换失败")
            from src.ui.configuration.roi_selector import RoiLayoutEditorDialog

            dialog = RoiLayoutEditorDialog(
                pixmap,
                self._coordinator.roi_layout(page_type),
                page_type,
                self.window(),
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            self._coordinator.save_roi_layout(page_type, dialog.get_layout())
            show_toast(self.window(), "识别区域已保存，将在下一次识别时生效。")
        except Exception as exc:
            logger.exception("保存 OCR ROI 配置失败")
            QMessageBox.warning(self.window(), "编辑识别区域", f"保存识别区域时出错:\n{exc}")

    def _reset_roi_layout(self, page_type: str) -> None:
        page_name = "对局攻略" if page_type == "match_guide" else "选将推荐"
        if QMessageBox.question(
            self.window(),
            "恢复默认识别区域",
            f"确定恢复{page_name}的默认识别区域吗？",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._coordinator.reset_roi_layout(page_type)
            show_toast(self.window(), "默认识别区域已恢复，将在下一次识别时生效。")
        except Exception as exc:
            logger.exception("恢复默认 OCR ROI 配置失败")
            QMessageBox.warning(self.window(), "恢复默认识别区域", f"恢复失败:\n{exc}")

    def _roi_layout_capture_button(self, page_type: str):
        return (
            self.ocr_polling_section.match_guide_roi_capture_button
            if page_type == "match_guide" else self.ocr_polling_section.hero_roi_capture_button
        )

    def _restore_roi_layout_button(self, page_type: str) -> None:
        button = self._roi_layout_capture_button(page_type)
        button.setEnabled(True)
        button.setText("截图编辑")

    # ────────────────────────────────────────────────
    # 轮询参数
    # ────────────────────────────────────────────────

    def resume_poll(self) -> None:
        """恢复已暂停的 OCR 轮询。"""
        if self._coordinator.resume_poll():
            self.ui_refresh_requested.emit()

    def _on_resume_poll(self) -> None:
        self.resume_poll()

    def _update_parameter_controls(self) -> None:
        """持续轮询关闭时，禁用只与轮询相关的控件。"""
        polling = self.ocr_polling_section
        polling_enabled = polling.poll_mode_check.isChecked()
        polling.poll_interval_spin.setEnabled(polling_enabled)
        polling.poll_idle_pause_check.setEnabled(polling_enabled)
        polling.auto_switch_tab_check.setEnabled(polling_enabled)
        polling_paused = polling_enabled and self._coordinator.poll_is_paused()
        polling.resume_button.setEnabled(polling_paused)
        polling.resume_button.setVisible(polling_paused)
