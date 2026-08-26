"""
名将杀 Agent - 后端选择对话框

提供 API 和浏览器两种生成方式的 Tab 切换选择。
传入成本估算时展示生成模式、Token 和预估费用。
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.config.env import PROVIDER_PRESETS, list_api_profiles
from src.ui.shared.widgets import DialogFooter, PageHeader

logger = logging.getLogger(__name__)


class BackendChooseDialog(QDialog):
    """后端选择对话框

    两个 Tab：
      - 「API 方式」：显示成本估算信息
      - 「浏览器方式」：显示提示信息

    通过 get_selected_backend() 获取用户选择。
    """

    def __init__(self, estimation: dict | None = None, title: str = "选择生成方式", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.setMinimumHeight(320)
        self._estimation = estimation
        self._selected_backend: str = "api"
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(self.windowTitle(), "确认生成方式与本次任务估算"))

        # 语料增强选择（对 API / 浏览器两种后端均生效）
        rag_layout = QHBoxLayout()
        rag_label = QLabel("语料增强：")
        self._rag_enhanced_radio = QRadioButton("RAG 语料增强（推荐）")
        self._rag_classic_radio = QRadioButton("经典模式（无 RAG 注入）")
        self._rag_enhanced_radio.setChecked(True)
        rag_group = QButtonGroup(self)
        rag_group.addButton(self._rag_enhanced_radio)
        rag_group.addButton(self._rag_classic_radio)
        self._rag_enhanced_radio.toggled.connect(self._on_rag_changed)
        rag_layout.addWidget(rag_label)
        rag_layout.addWidget(self._rag_enhanced_radio)
        rag_layout.addWidget(self._rag_classic_radio)
        rag_layout.addStretch()
        layout.addLayout(rag_layout)

        # Tab 切换
        self._tabs = QTabWidget()

        # Tab 1: API 方式
        api_tab = QWidget()
        api_layout = QVBoxLayout(api_tab)

        self._api_estimation_labels: list[QLabel] = []
        self._api_message_label: QLabel | None = None
        if self._estimation:
            for text in self._estimation_lines():
                label = QLabel(text)
                label.setStyleSheet("font-size: 14px; padding: 2px 0;")
                api_layout.addWidget(label)
                self._api_estimation_labels.append(label)

            self._api_message_label = QLabel(self._estimation.get("message", ""))
            self._api_message_label.setStyleSheet("color: gray; font-size: 13px; padding: 4px 0;")
            self._api_message_label.setVisible(bool(self._estimation.get("message", "")))
            api_layout.addWidget(self._api_message_label)
            if not self._has_available_api():
                hint = QLabel("未配置可用 API，请先在「API 配置」中新增并启用档案")
                hint.setStyleSheet("color: gray; font-size: 13px; padding: 4px 0;")
                api_layout.addWidget(hint)
        else:
            api_layout.addWidget(QLabel("API 模式：通过 API 直连生成"))
            api_layout.addWidget(QLabel("需要配置并启用至少一个 API 档案"))
            api_layout.addWidget(QLabel("优点：速度快，支持 Token 统计和费用估算"))
            api_layout.addWidget(QLabel("缺点：需要付费 API Key"))
            if not self._has_available_api():
                hint = QLabel("未配置可用 API，请先在「API 配置」中新增并启用档案")
                hint.setStyleSheet("color: gray; font-size: 13px; padding: 4px 0;")
                api_layout.addWidget(hint)

        api_layout.addStretch()
        self._tabs.addTab(api_tab, "API 方式")

        # Tab 2: 浏览器方式
        browser_tab = QWidget()
        browser_layout = QVBoxLayout(browser_tab)

        browser_info = [
            "浏览器模式：通过 Playwright + Edge 自动化操作 DeepSeek 网页版",
            "",
            "使用前请确保：",
            "  1. Edge 浏览器已安装",
            "  2. 已登录 https://chat.deepseek.com/",
            "  3. 完全关闭所有 Edge 窗口后重试",
            "",
            "优点：免费，无需 API Key",
            "缺点：速度较慢，需要浏览器可见运行，不支持 Token 统计",
        ]
        for line in browser_info:
            label = QLabel(line)
            label.setStyleSheet("font-size: 13px; padding: 1px 0;")
            browser_layout.addWidget(label)

        browser_layout.addStretch()
        self._tabs.addTab(browser_tab, "浏览器方式")

        layout.addWidget(self._tabs, 1)

        footer = DialogFooter(accept_text="确定执行", cancel_text="取消")
        footer.accepted.connect(self._on_accept)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)

    def _estimation_lines(self) -> list[str]:
        """根据当前 estimation 生成 API 成本展示行。"""
        est = self._estimation or {}
        mode_text = {
            "all": "全量获取",
            "incremental": "增量获取",
            "specific": "指定获取",
            "synergy": "相性生成",
        }
        m = mode_text.get(est.get("mode", ""), "未知")
        cost = est.get("estimated_cost_cny")
        cost_text = f"预估费用: CNY {cost:.4f}" if cost is not None else "预估费用: 无法自动估算"
        return [
            f"模式: {m}",
            f"模型: {est.get('model', '未提供')}",
            f"需要生成的项数: {est.get('items', 0)}",
            f"预估输入 Token: {est.get('estimated_input_tokens', 0):,}",
            f"预估输出 Token: {est.get('estimated_output_tokens', 0):,}",
            f"合计 Token: {est.get('estimated_tokens', 0):,}",
            cost_text,
        ]

    def _on_rag_changed(self) -> None:
        """RAG 选择切换时重算 API 成本估算（经典模式输入更少）。"""
        if not self._estimation or self._estimation.get("estimate_kind") not in ("guide", "synergy"):
            return
        if not getattr(self, "_api_estimation_labels", None):
            return
        self._recompute_estimation()

    def _has_available_api(self) -> bool:
        """是否有可用的 API 档案（enabled + URL 非空 + 供应商 Key 语义），与生成链路一致。"""
        for p in list_api_profiles():
            if not p.get("enabled", True) or not p.get("api_url"):
                continue
            provider = p.get("provider", "deepseek")
            if PROVIDER_PRESETS.get(provider, {}).get("requires_key", True) and not p.get("has_key"):
                continue
            return True
        return False

    def _recompute_estimation(self) -> None:
        """按当前 RAG 选择重算 estimation 的成本字段并刷新标签。"""
        from src.scraper.ai.prompt_utils import estimate_item_cost
        est = self._estimation
        new = estimate_item_cost(
            est.get("items", 0),
            est["estimate_kind"],
            est.get("model"),
            use_rag=self.get_selected_rag(),
        )
        for key in ("estimated_input_tokens", "estimated_output_tokens", "estimated_tokens", "estimated_cost_cny", "message"):
            est[key] = new[key]
        for label, text in zip(self._api_estimation_labels, self._estimation_lines()):
            label.setText(text)
        if self._api_message_label is not None:
            self._api_message_label.setText(est.get("message", ""))
            self._api_message_label.setVisible(bool(est.get("message", "")))

    def get_selected_rag(self) -> bool:
        """获取语料增强选择：True = RAG 语料注入，False = 经典模式（无 RAG）。"""
        return self._rag_enhanced_radio.isChecked()

    def _on_accept(self) -> None:
        """确定时记录当前 Tab 对应的后端；API 方式无可用档案时拦截引导配置。"""
        idx = self._tabs.currentIndex()
        backend = "browser" if idx == 1 else "api"
        if backend == "api" and not self._has_available_api():
            QMessageBox.warning(
                self,
                "未配置可用 API",
                "请先在「配置 → API 配置」中新增并启用至少一个 API 档案。",
            )
            return
        self._selected_backend = backend
        self.accept()

    def get_selected_backend(self) -> str:
        """获取用户选择的后端类型

        Returns:
            "api" 或 "browser"
        """
        return self._selected_backend
