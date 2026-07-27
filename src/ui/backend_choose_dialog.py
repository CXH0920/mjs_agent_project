"""
名将杀 Agent - 后端选择对话框

提供 API 和浏览器两种生成方式的 Tab 切换选择。
攻略生成时展示成本估算，相性生成时仅展示模式说明。
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

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

        # Tab 切换
        self._tabs = QTabWidget()

        # Tab 1: API 方式
        api_tab = QWidget()
        api_layout = QVBoxLayout(api_tab)

        if self._estimation:
            mode_text = {
                "all": "全量获取",
                "incremental": "增量获取",
                "specific": "指定获取",
            }
            m = mode_text.get(self._estimation.get("mode", ""), "未知")

            cost = self._estimation.get("estimated_cost_cny")
            cost_text = f"预估费用: CNY {cost:.4f}" if cost is not None else "预估费用: 无法自动估算"
            info_lines = [
                f"模式: {m}",
                f"模型: {self._estimation.get('model', '未提供')}",
                f"需要生成的项数: {self._estimation.get('items', 0)}",
                f"预估输入 Token: {self._estimation.get('estimated_input_tokens', 0):,}",
                f"预估输出 Token: {self._estimation.get('estimated_output_tokens', 0):,}",
                f"合计 Token: {self._estimation.get('estimated_tokens', 0):,}",
                cost_text,
            ]

            for line in info_lines:
                label = QLabel(line)
                label.setStyleSheet("font-size: 14px; padding: 2px 0;")
                api_layout.addWidget(label)

            msg = self._estimation.get("message", "")
            if msg:
                msg_label = QLabel(msg)
                msg_label.setStyleSheet("color: gray; font-size: 13px; padding: 4px 0;")
                api_layout.addWidget(msg_label)
        else:
            api_layout.addWidget(QLabel("API 模式：通过 DeepSeek API 直连生成"))
            api_layout.addWidget(QLabel("需要配置 DEEPSEEK_API_KEY"))
            api_layout.addWidget(QLabel("优点：速度快，支持 Token 统计和费用估算"))
            api_layout.addWidget(QLabel("缺点：需要付费 API Key"))

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

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        ok_btn = QPushButton("确定执行")
        ok_btn.setStyleSheet("padding: 6px 24px;")
        ok_btn.clicked.connect(self._on_accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet("padding: 6px 24px;")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def _on_accept(self) -> None:
        """确定时记录当前 Tab 对应的后端"""
        idx = self._tabs.currentIndex()
        self._selected_backend = "browser" if idx == 1 else "api"
        self.accept()

    def get_selected_backend(self) -> str:
        """获取用户选择的后端类型

        Returns:
            "api" 或 "browser"
        """
        return self._selected_backend
