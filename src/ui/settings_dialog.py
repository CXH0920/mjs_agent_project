"""
名将杀 Agent - API 配置对话框

提供图形界面编辑 config.env 中的 API 配置项。
支持读取当前配置、原子写入、新建配置文件。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from src.config.env import (
    DEFAULT_ENV_FILE,
    DEFAULT_PRICING_FILE,
    load_pricing_config,
    parse_env_file,
    save_env_file,
    save_pricing_config,
)

logger = logging.getLogger(__name__)

PRICING_UNIT_LABEL = "百万tokens"

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

    def __init__(
        self,
        env_path: Optional[Path] = None,
        parent=None,
        pricing_path: Optional[Path] = None,
    ):
        super().__init__(parent)
        self._env_path = env_path or DEFAULT_ENV_FILE
        self._pricing_path = pricing_path or DEFAULT_PRICING_FILE
        self._text_widgets: dict[str, QLineEdit] = {}
        self._spin_widgets: dict[str, QSpinBox] = {}
        self._pricing_table: QTableWidget
        self._currency_widget: QLineEdit
        self._unit_widget: QLineEdit
        self._updated_at_widget: QLineEdit

        self.setWindowTitle("API 配置")
        self.setMinimumSize(480, 350)
        self._setup_ui()
        self._load_configs()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        """构建对话框界面"""
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_parameter_tab(), "参数配置")
        tabs.addTab(self._build_pricing_tab(), "价格配置")
        layout.addWidget(tabs)

        # 保存和取消对两个页签统一生效。
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

    def _build_parameter_tab(self) -> QWidget:
        """构建原 API 参数配置表单。"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

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
        layout.addStretch()
        return tab

    def _build_pricing_tab(self) -> QWidget:
        """构建模型价格维护页签。"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        metadata = QFormLayout()
        self._currency_widget = QLineEdit()
        self._unit_widget = QLineEdit()
        self._updated_at_widget = QLineEdit()
        self._currency_widget.setPlaceholderText("例如 CNY")
        self._unit_widget.setPlaceholderText(PRICING_UNIT_LABEL)
        self._updated_at_widget.setPlaceholderText("例如 2026-07-22")
        metadata.addRow("币种:", self._currency_widget)
        metadata.addRow("计价单位:", self._unit_widget)
        metadata.addRow("更新时间:", self._updated_at_widget)
        layout.addLayout(metadata)

        self._pricing_table = QTableWidget(0, 4)
        self._pricing_table.setHorizontalHeaderLabels(
            ["模型名称", "输入", "输出", "缓存命中"]
        )
        self._pricing_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._pricing_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._pricing_table.setAlternatingRowColors(True)
        self._pricing_table.horizontalHeader().setStretchLastSection(True)
        self._pricing_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._pricing_table)

        row_buttons = QHBoxLayout()
        add_btn = QPushButton("新增模型")
        add_btn.clicked.connect(lambda: self._add_pricing_row())
        remove_btn = QPushButton("删除选中模型")
        remove_btn.clicked.connect(self._remove_pricing_row)
        row_buttons.addWidget(add_btn)
        row_buttons.addWidget(remove_btn)
        row_buttons.addStretch()
        layout.addLayout(row_buttons)
        return tab

    # ---------------------------------------------------------------
    # 加载 / 保存
    # ---------------------------------------------------------------

    def _load_configs(self) -> None:
        """加载参数配置和模型价格配置。"""
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

        pricing = load_pricing_config(self._pricing_path)
        self._currency_widget.setText(pricing["currency"])
        unit = pricing["unit"]
        self._unit_widget.setText(
            PRICING_UNIT_LABEL if unit == "per_million_tokens" else unit
        )
        self._updated_at_widget.setText(pricing["updated_at"])
        for model, values in pricing["models"].items():
            if isinstance(values, dict):
                self._add_pricing_row(model, values)

    @staticmethod
    def _price_spinbox(value=0.0) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(0, 1_000_000)
        widget.setDecimals(2)
        widget.setSingleStep(0.01)
        widget.setValue(float(value or 0))
        return widget

    def _add_pricing_row(self, model: str = "", values: Optional[dict] = None) -> None:
        values = values or {}
        row = self._pricing_table.rowCount()
        self._pricing_table.insertRow(row)
        self._pricing_table.setItem(row, 0, QTableWidgetItem(model))
        self._pricing_table.setCellWidget(
            row, 1, self._price_spinbox(values.get("input_per_million", 0))
        )
        self._pricing_table.setCellWidget(
            row, 2, self._price_spinbox(values.get("output_per_million", 0))
        )
        cached = values.get("cached_input_per_million")
        cached_widget = QLineEdit("" if cached is None else str(cached))
        cached_widget.setPlaceholderText("可选")
        self._pricing_table.setCellWidget(row, 3, cached_widget)

    def _remove_pricing_row(self) -> None:
        row = self._pricing_table.currentRow()
        if row >= 0:
            self._pricing_table.removeRow(row)

    def _collect_pricing_config(self) -> dict:
        """读取并校验价格页数据，避免将无效价格写入配置文件。"""
        models: dict[str, dict] = {}
        for row in range(self._pricing_table.rowCount()):
            item = self._pricing_table.item(row, 0)
            model = item.text().strip() if item else ""
            if not model:
                raise ValueError(f"第 {row + 1} 行模型名称不能为空")
            if model in models:
                raise ValueError(f"模型 {model} 重复配置")

            input_price = self._pricing_table.cellWidget(row, 1).value()
            output_price = self._pricing_table.cellWidget(row, 2).value()
            values = {
                "input_per_million": input_price,
                "output_per_million": output_price,
            }
            cached_text = self._pricing_table.cellWidget(row, 3).text().strip()
            if cached_text:
                try:
                    cached_price = float(cached_text)
                except ValueError as error:
                    raise ValueError(f"模型 {model} 的缓存输入价格无效") from error
                if cached_price < 0:
                    raise ValueError(f"模型 {model} 的缓存输入价格不能为负数")
                values["cached_input_per_million"] = cached_price
            models[model] = values

        return {
            "currency": self._currency_widget.text().strip() or "CNY",
            "unit": self._unit_widget.text().strip() or PRICING_UNIT_LABEL,
            "updated_at": self._updated_at_widget.text().strip(),
            "models": models,
        }

    def _on_save(self) -> None:
        """保存配置到 config.env"""
        try:
            pricing_data = self._collect_pricing_config()
        except ValueError as error:
            QMessageBox.warning(self, "价格配置无效", str(error))
            return

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
            save_pricing_config(self._pricing_path, pricing_data)
            QMessageBox.information(self, "保存成功", "配置已保存")
            self.accept()
        except Exception as e:
            logger.exception("保存配置失败")
            QMessageBox.critical(self, "保存失败", f"无法写入配置文件:\n{e}")
