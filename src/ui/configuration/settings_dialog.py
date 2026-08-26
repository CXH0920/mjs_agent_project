"""
名将杀 Agent - API 配置对话框

「参数配置」Tab：多 API 档案列表 + 编辑面板 + 运行参数（config.env 标量）。
「价格配置」Tab：模型价格维护。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.config.env import (
    DEFAULT_ENV_FILE,
    DEFAULT_PRICING_FILE,
    DEFAULT_PROFILES_FILE,
    PROVIDER_LABELS,
    PROVIDER_PRESETS,
    load_api_profiles,
    load_pricing_config,
    parse_env_file,
    save_api_profiles,
    save_env_file,
    save_pricing_config,
)
from src.ui.shared.widgets import DialogFooter, PageHeader, show_toast

logger = logging.getLogger(__name__)

PRICING_UNIT_LABEL = "百万tokens"

# 运行参数（写入 config.env 标量，非 API 档案）：(标签, 环境变量键, 默认值, 最小值, 最大值)
SPIN_FIELDS = [
    ("每分钟请求数", "REQUESTS_PER_MINUTE", 30, 1, 120),
    ("HTTP 超时(秒)", "HTTP_TIMEOUT", 300, 10, 600),
    ("最大重试次数", "MAX_RETRIES", 3, 0, 10),
]


class SettingsDialog(QDialog):
    """API 配置编辑对话框

    以档案列表 + 编辑面板管理多套 API 配置（api_profiles.json），
    同时维护 config.env 中的运行时参数与 model_pricing.json 价格表。
    """

    def __init__(
        self,
        env_path: Optional[Path] = None,
        parent=None,
        pricing_path: Optional[Path] = None,
        profiles_path: Optional[Path] = None,
    ):
        super().__init__(parent)
        self._env_path = env_path or DEFAULT_ENV_FILE
        self._pricing_path = pricing_path or DEFAULT_PRICING_FILE
        self._profiles_path = profiles_path or DEFAULT_PROFILES_FILE
        self._spin_widgets: dict[str, QSpinBox] = {}
        self._pricing_table: QTableWidget
        self._currency_widget: QLineEdit
        self._unit_widget: QLineEdit
        self._updated_at_widget: QLineEdit
        # API 档案内存工作副本（含 Key——配置编辑器为可信路径，Key 不入日志/UI 文本）
        self._profiles_data: dict = {"version": 1, "profiles": []}
        self._profile_table: QTableWidget
        self._profile_widgets: dict[str, QWidget] = {}
        self._edit_index: int | None = None
        self._loading = False

        self.setWindowTitle("API 配置")
        self.setMinimumSize(640, 480)
        self._setup_ui()
        self._load_configs()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        """构建对话框界面"""
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader("API 配置", "管理 API 档案与模型计价信息"))
        tabs = QTabWidget()
        tabs.addTab(self._build_parameter_tab(), "参数配置")
        tabs.addTab(self._build_pricing_tab(), "价格配置")
        layout.addWidget(tabs)

        self._footer = DialogFooter(accept_text="保存", cancel_text="取消")
        self._footer.accepted.connect(self._on_save)
        self._footer.rejected.connect(self.reject)
        layout.addWidget(self._footer)

    def _build_parameter_tab(self) -> QWidget:
        """构建参数配置页：档案列表 + 编辑面板 + 运行参数。"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_profile_list_pane())
        splitter.addWidget(self._build_profile_edit_pane())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        # 运行参数（config.env 标量，与 API 档案分离）
        runtime_form = QFormLayout()
        runtime_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        for label, key, default, min_val, max_val in SPIN_FIELDS:
            widget = QSpinBox()
            widget.setRange(min_val, max_val)
            widget.setValue(default)
            self._spin_widgets[key] = widget
            runtime_form.addRow(f"{label}:", widget)
        layout.addLayout(runtime_form)
        return tab

    def _build_profile_list_pane(self) -> QWidget:
        """档案列表区：表格 + 操作按钮。"""
        pane = QWidget()
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)

        self._profile_table = QTableWidget(0, 4)
        self._profile_table.setHorizontalHeaderLabels(["名称", "供应商", "状态", "Key"])
        self._profile_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._profile_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._profile_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._profile_table.setAlternatingRowColors(True)
        self._profile_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._profile_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._profile_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._profile_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._profile_table.horizontalHeader().setStretchLastSection(False)
        self._profile_table.verticalHeader().setVisible(False)
        self._profile_table.currentCellChanged.connect(lambda *_: self._on_profile_selected())
        pane_layout.addWidget(self._profile_table)

        buttons = QHBoxLayout()
        add_btn = QPushButton("新增")
        add_btn.clicked.connect(self._on_add_profile)
        remove_btn = QPushButton("删除")
        remove_btn.clicked.connect(self._on_remove_profile)
        toggle_btn = QPushButton("启用/停用")
        toggle_btn.clicked.connect(self._on_toggle_enabled)
        for button in (add_btn, remove_btn, toggle_btn):
            buttons.addWidget(button)
        buttons.addStretch()
        pane_layout.addLayout(buttons)
        return pane

    def _build_profile_edit_pane(self) -> QWidget:
        """档案编辑面板：name/provider/api_key/url/model/enabled/note。"""
        pane = QWidget()
        form = QFormLayout(pane)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._name_widget = QLineEdit()
        self._name_widget.setPlaceholderText("档案名称（全局唯一）")
        form.addRow("名称:", self._name_widget)

        self._provider_widget = QComboBox()
        for key, label in PROVIDER_LABELS.items():
            self._provider_widget.addItem(label, userData=key)
        self._provider_widget.currentIndexChanged.connect(lambda _: self._on_provider_changed())
        form.addRow("供应商:", self._provider_widget)

        self._api_key_widget = QLineEdit()
        self._api_key_widget.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_widget.setPlaceholderText("已配置（留空保持不变）／未配置")
        clear_key_btn = QPushButton("清除")
        clear_key_btn.setToolTip("把当前档案的 API Key 清空为「未配置」（仅留空输入框是保持原值）")
        clear_key_btn.clicked.connect(self._on_clear_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self._api_key_widget, 1)
        key_row.addWidget(clear_key_btn)
        form.addRow("API Key:", key_row)

        self._api_url_widget = QLineEdit()
        form.addRow("API URL:", self._api_url_widget)

        self._model_widget = QLineEdit()
        self._model_widget.setPlaceholderText("留空使用服务默认模型")
        form.addRow("模型:", self._model_widget)

        self._enabled_widget = QCheckBox("启用（同时只允许一个启用=当前使用 API）")
        form.addRow("", self._enabled_widget)

        self._note_widget = QLineEdit()
        self._note_widget.setPlaceholderText("可选备注，如“主备账号”")
        form.addRow("备注:", self._note_widget)

        self._profile_widgets = {
            "name": self._name_widget,
            "provider": self._provider_widget,
            "api_key": self._api_key_widget,
            "api_url": self._api_url_widget,
            "model": self._model_widget,
            "enabled": self._enabled_widget,
            "note": self._note_widget,
        }
        return pane

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
    # 档案列表 / 编辑面板
    # ---------------------------------------------------------------

    def _load_profiles_to_table(self) -> None:
        """按内存工作副本刷新档案列表（Key 不回显，仅 has_key 指示）。"""
        self._loading = True
        profiles = self._profiles_data.get("profiles", [])
        self._profile_table.setRowCount(len(profiles))
        for row, profile in enumerate(profiles):
            self._profile_table.setItem(row, 0, QTableWidgetItem(profile.get("name", "")))
            self._profile_table.setItem(row, 1, QTableWidgetItem(
                PROVIDER_LABELS.get(profile.get("provider", ""), profile.get("provider", ""))))
            status = "● 启用" if profile.get("enabled", True) else "○ 停用"
            self._profile_table.setItem(row, 2, QTableWidgetItem(status))
            self._profile_table.setItem(row, 3, QTableWidgetItem(
                "已配置" if profile.get("api_key") else "未配置"))
        self._loading = False

    def _on_profile_selected(self) -> None:
        """列表行切换：先提交当前面板草稿，再回填新选中行。"""
        if self._loading:
            return
        self._commit_panel()
        row = self._profile_table.currentRow()
        if 0 <= row < len(self._profiles_data.get("profiles", [])):
            self._load_profile_to_panel(row)
        else:
            self._clear_profile_panel()

    def _load_profile_to_panel(self, row: int) -> None:
        """把选中档案回填到编辑面板（Key 框留空，placeholder 反映已配置状态）。"""
        profile = self._profiles_data["profiles"][row]
        self._edit_index = row
        self._loading = True
        self._name_widget.setText(profile.get("name", ""))
        index = self._provider_widget.findData(profile.get("provider", "openai-compatible"))
        self._provider_widget.setCurrentIndex(index if index >= 0 else 0)
        self._api_key_widget.clear()
        self._api_key_widget.setPlaceholderText(
            "已配置（留空保持不变）" if profile.get("api_key") else "未配置")
        self._api_url_widget.setText(profile.get("api_url", ""))
        self._model_widget.setText(profile.get("model", ""))
        self._enabled_widget.setChecked(profile.get("enabled", True))
        self._note_widget.setText(profile.get("note", ""))
        self._loading = False

    def _clear_profile_panel(self) -> None:
        self._edit_index = None
        self._loading = True
        for key in ("name", "api_url", "model", "note"):
            self._profile_widgets[key].setText("")
        self._api_key_widget.clear()
        self._api_key_widget.setPlaceholderText("未配置")
        self._provider_widget.setCurrentIndex(0)
        self._enabled_widget.setChecked(True)
        self._loading = False

    def _commit_panel(self) -> None:
        """把编辑面板写回当前编辑的档案（Key 留空表示保持原值）。

        纯写入数据模型，不刷新表格、不改选中行——避免在 _on_profile_selected
        等选择回调中触发 currentCellChanged 重入，导致选中行被弹回旧行、
        面板内容写回错误档案。表格刷新与选中恢复由各调用方负责。
        """
        if self._edit_index is None or self._edit_index >= len(self._profiles_data["profiles"]):
            return
        profile = self._profiles_data["profiles"][self._edit_index]
        profile["name"] = self._name_widget.text().strip()
        provider = self._provider_widget.currentData()
        if provider:
            profile["provider"] = provider
        key = self._api_key_widget.text().strip()
        if key:
            profile["api_key"] = key
        profile["api_url"] = self._api_url_widget.text().strip()
        profile["model"] = self._model_widget.text().strip()
        profile["enabled"] = self._enabled_widget.isChecked()
        profile["note"] = self._note_widget.text().strip()

    def _on_provider_changed(self) -> None:
        """切换供应商时按预设表预填 URL/模型（字段为空或仍为某预设值时才覆盖，保留自定义）。"""
        if self._loading:
            return
        provider = self._provider_widget.currentData()
        preset = PROVIDER_PRESETS.get(provider, {})
        preset_urls = {p["api_url"] for p in PROVIDER_PRESETS.values() if p["api_url"]}
        url = self._api_url_widget.text().strip()
        if not url or url in preset_urls:
            self._api_url_widget.setText(preset.get("api_url", ""))
        preset_models = {p["model"] for p in PROVIDER_PRESETS.values() if p["model"]}
        model = self._model_widget.text().strip()
        if not model or model in preset_models:
            self._model_widget.setText(preset.get("model", ""))

    def _on_add_profile(self) -> None:
        """新增档案：追加并选中编辑。同时只允许一个启用——当前已有启用档案则新增默认停用。"""
        self._commit_panel()
        preset = PROVIDER_PRESETS["deepseek"]
        has_enabled = any(p.get("enabled", True) for p in self._profiles_data["profiles"])
        self._profiles_data["profiles"].append({
            "name": "",
            "provider": "deepseek",
            "api_key": "",
            "api_url": preset["api_url"],
            "model": preset["model"],
            "enabled": not has_enabled,
            "note": "",
        })
        self._load_profiles_to_table()
        new_row = self._profile_table.rowCount() - 1
        self._profile_table.selectRow(new_row)

    def _on_remove_profile(self) -> None:
        """删除当前选中档案，选中行停在删除位置（或末行），面板刷新到新选中行。"""
        if self._edit_index is None:
            return
        profiles = self._profiles_data["profiles"]
        if self._edit_index >= len(profiles):
            return
        name = profiles[self._edit_index].get("name") or f"第 {self._edit_index + 1} 个"
        if not self._confirm("删除档案", f"确认删除档案「{name}」？"):
            return
        removed_index = self._edit_index
        del profiles[removed_index]
        self._edit_index = None
        self._load_profiles_to_table()
        if profiles:
            # 用删除后的长度算 target（删末行不越界），显式刷新面板（不依赖 selectRow 信号）
            target = min(removed_index, len(profiles) - 1)
            self._loading = True
            self._profile_table.selectRow(target)
            self._loading = False
            self._load_profile_to_panel(target)
        else:
            self._clear_profile_panel()

    def _on_toggle_enabled(self) -> None:
        """切换启用/停用（互斥：启用当前档案则停用其他所有）。"""
        self._commit_panel()
        if self._edit_index is None:
            return
        profiles = self._profiles_data["profiles"]
        p = profiles[self._edit_index]
        will_enable = not p.get("enabled", True)
        p["enabled"] = will_enable
        if will_enable:
            for i, other in enumerate(profiles):
                if i != self._edit_index:
                    other["enabled"] = False
        self._load_profiles_to_table()
        if self._edit_index is not None:
            self._profile_table.selectRow(self._edit_index)

    def _on_clear_key(self) -> None:
        """清除当前档案的 API Key（置空=未配置；区别于留空输入框=保持原值）。"""
        if self._edit_index is None or self._edit_index >= len(self._profiles_data["profiles"]):
            return
        self._profiles_data["profiles"][self._edit_index]["api_key"] = ""
        self._api_key_widget.clear()
        self._api_key_widget.setPlaceholderText("未配置")
        self._load_profiles_to_table()
        if self._edit_index is not None:
            self._profile_table.selectRow(self._edit_index)

    def _collect_profiles(self) -> dict:
        """读取并校验档案页数据：名称唯一非空、URL 合法、供应商语义 Key 必填。"""
        self._commit_panel()
        profiles = self._profiles_data.get("profiles", [])
        seen: set[str] = set()
        for i, p in enumerate(profiles):
            name = p.get("name", "").strip()
            if not name:
                raise ValueError(f"第 {i + 1} 个档案名称不能为空")
            if name in seen:
                raise ValueError(f"档案名称重复: {name}")
            seen.add(name)
            provider = p.get("provider", "")
            if provider not in PROVIDER_PRESETS:
                raise ValueError(f"档案 {name} 的供应商无效")
            url = p.get("api_url", "").strip()
            if not url:
                raise ValueError(f"档案 {name} 的 API URL 不能为空")
            if not (url.startswith("http://") or url.startswith("https://")):
                raise ValueError(f"档案 {name} 的 URL 必须以 http:// 或 https:// 开头")
            if p.get("enabled", True) and PROVIDER_PRESETS[provider]["requires_key"] and not p.get("api_key", "").strip():
                raise ValueError(f"档案 {name} 的供应商 {PROVIDER_LABELS[provider]} 启用时必须填写 API Key（停用可留空）")
        return self._profiles_data

    def _confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(
            self, title, text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    # ---------------------------------------------------------------
    # 加载 / 保存
    # ---------------------------------------------------------------

    def _load_configs(self) -> None:
        """加载档案、运行参数和模型价格配置。"""
        self._profiles_data = load_api_profiles(self._profiles_path)
        self._load_profiles_to_table()
        if self._profiles_data.get("profiles"):
            self._profile_table.selectRow(0)
        else:
            self._clear_profile_panel()

        data = parse_env_file(self._env_path)
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
        """保存档案、运行参数与价格配置。"""
        try:
            profiles_data = self._collect_profiles()
            pricing_data = self._collect_pricing_config()
        except ValueError as error:
            QMessageBox.warning(self, "配置无效", str(error))
            return

        self._footer.set_busy(True, "正在保存...")

        # 运行参数（config.env 标量；DEEPSEEK_* 旧键由 save_env_file 原地保留，不再由此处写入）
        data: dict[str, str] = {}
        for key, widget in self._spin_widgets.items():
            data[key] = str(widget.value())

        try:
            self._env_path.parent.mkdir(parents=True, exist_ok=True)
            save_env_file(self._env_path, data)
            save_pricing_config(self._pricing_path, pricing_data)
            save_api_profiles(profiles_data, self._profiles_path)
            show_toast(self, "API 配置已保存", duration=500)
            QTimer.singleShot(500, self.accept)
        except Exception as e:
            self._footer.set_busy(False)
            logger.exception("保存配置失败")
            QMessageBox.critical(self, "保存失败", f"无法写入配置文件:\n{e}")
