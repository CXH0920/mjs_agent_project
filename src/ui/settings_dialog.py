"""
名将杀 Agent - API 配置对话框

提供图形界面编辑 config.env 中的 API 配置项。
支持读取当前配置、原子写入、新建配置文件。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)
from PySide6.QtCore import Qt

from src.config.env import parse_env_file, save_env_file, DEFAULT_ENV_FILE

logger = logging.getLogger(__name__)

# 配置字段定义：(标签, 环境变量键, 控件类型, 默认值, 最小值, 最大值)

TEXT_FIELDS = [
    ("API Key", "DEEPSEEK_API_KEY", "", None, None),
    ("API URL", "DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions", None, None),
    ("模型名称", "DEEPSEEK_MODEL", "deepseek-v4-pro", None, None),
]

SPIN_FIELDS = [
    ("每分钟请求数", "REQUESTS_PER_MINUTE", 30, 1, 120),
    ("HTTP 超时(秒)", "HTTP_TIMEOUT", 300, 10, 600),
    ("最大重试次数", "MAX_RETRIES", 3, 0, 10),
]


class SettingsDialog(QDialog):
    """API 配置编辑对话框

    以表单形式编辑 config.env 中的所有配置项，支持新建和原子写入。
    """

    def __init__(self, env_path: Optional[Path] = None, parent=None):
        super().__init__(parent)
        self._env_path = env_path or DEFAULT_ENV_FILE
        self._text_widgets: dict[str, QLineEdit] = {}
        self._spin_widgets: dict[str, QSpinBox] = {}

        self.setWindowTitle("API 配置")
        self.setMinimumWidth(450)
        self._setup_ui()
        self._load_config()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        """构建对话框界面"""
        layout = QVBoxLayout(self)

        # 表单布局
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # 文本输入字段
        for label, key, default, _, _ in TEXT_FIELDS:
            widget = QLineEdit()
            if key == "DEEPSEEK_API_KEY":
                widget.setEchoMode(QLineEdit.EchoMode.Password)
                widget.setPlaceholderText("输入 DeepSeek API Key")
            else:
                widget.setPlaceholderText(default or "")
            self._text_widgets[key] = widget
            form.addRow(f"{label}:", widget)

        # 数值输入字段
        for label, key, default, min_val, max_val in SPIN_FIELDS:
            widget = QSpinBox()
            widget.setRange(min_val, max_val)
            widget.setValue(default)
            self._spin_widgets[key] = widget
            form.addRow(f"{label}:", widget)

        layout.addLayout(form)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        save_btn = QPushButton("保存")
        save_btn.setStyleSheet("padding: 6px 24px;")
        save_btn.clicked.connect(self._on_save)
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet("padding: 6px 24px;")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    # ---------------------------------------------------------------
    # 加载 / 保存
    # ---------------------------------------------------------------

    def _load_config(self) -> None:
        """从 config.env 加载当前配置到表单"""
        data = parse_env_file(self._env_path)

        for key, widget in self._text_widgets.items():
            if key in data:
                widget.setText(data[key])

        for key, widget in self._spin_widgets.items():
            if key in data:
                try:
                    widget.setValue(int(data[key]))
                except (ValueError, TypeError):
                    logger.warning("配置字段 %s 值无法解析为整数: %s", key, data[key])

    def _on_save(self) -> None:
        """保存配置到 config.env"""
        # 收集数据
        data: dict[str, str] = {}
        for key, widget in self._text_widgets.items():
            value = widget.text().strip()
            if value:
                data[key] = value
        for key, widget in self._spin_widgets.items():
            data[key] = str(widget.value())

        try:
            # 确保目录存在
            self._env_path.parent.mkdir(parents=True, exist_ok=True)
            save_env_file(self._env_path, data)
            QMessageBox.information(self, "保存成功", "配置已保存")
            self.accept()
        except Exception as e:
            logger.exception("保存配置失败")
            QMessageBox.critical(self, "保存失败", f"无法写入配置文件:\n{e}")
