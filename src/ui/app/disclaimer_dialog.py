"""
名将杀 Agent - 免责声明弹窗

免责声明文本版本变化时在启动阶段模态展示（接受状态见 src/config/disclaimer_state.py）。
未勾选同意时确定按钮禁用；直接关闭视为拒绝，由调用方退出应用。
精简版 7 条与 TERMS.md 完整条款逐条对应，条款实质修订时同步递增 DISCLAIMER_VERSION。
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QCheckBox, QDialog, QLabel, QVBoxLayout
from src.config.disclaimer_state import DISCLAIMER_VERSION
from src.ui.shared.widgets import DialogFooter, PageHeader

logger = logging.getLogger(__name__)

# 精简版 7 条，与 TERMS.md 第 1-8 节对应
_DISCLAIMER_ITEMS = (
    "本项目为个人技术学习与作品集展示项目，与《名将杀》游戏及其运营方无任何关联，未经运营方授权。",
    "仅供个人学习、研究与技术交流使用，禁止任何商业使用。",
    "严禁改造为游戏外挂：自动点击、自动选将、自动战斗、进程注入、内存修改、hook、反作弊绕过等功能一律不提供，相关请求将被拒绝。",
    "通过本项目采集的数据仅限本地查阅，禁止二次发布。",
    "本工具默认下载武将头像用于本地界面展示，可能涉及版权风险，请自行评估并承担使用本工具的法律风险。",
    "AI 生成内容由第三方大语言模型提供，准确性未经保证，仅供参考，不构成任何竞技建议。",
    "本项目按“现状”提供，不附带任何担保。完整条款见随附 LICENSE（附加使用条款）与 TERMS.md。",
)


class DisclaimerDialog(QDialog):
    """启动免责声明：勾选同意后确定按钮才可用，关闭视为拒绝。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("使用须知与免责声明")
        self.setMinimumWidth(540)
        self.setMinimumHeight(480)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(self.windowTitle(), "首次使用或条款更新后需确认"))

        intro = QLabel("使用本应用前请仔细阅读以下条款：")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        items_text = "\n".join(
            f"{index}. {item}" for index, item in enumerate(_DISCLAIMER_ITEMS, start=1)
        )
        self._items_label = QLabel(items_text)
        self._items_label.setWordWrap(True)
        layout.addWidget(self._items_label)

        self._version_label = QLabel(f"条款版本 v{DISCLAIMER_VERSION}")
        self._version_label.setWordWrap(True)
        layout.addWidget(self._version_label)

        layout.addStretch()

        self._accept_checkbox = QCheckBox("我已阅读并同意上述条款")
        self._accept_checkbox.toggled.connect(self._on_accept_toggled)
        layout.addWidget(self._accept_checkbox)

        self._footer = DialogFooter(accept_text="确定", show_cancel=False)
        self._footer.accept_button.setEnabled(False)
        self._footer.accepted.connect(self.accept)
        layout.addWidget(self._footer)

    def _on_accept_toggled(self, checked: bool) -> None:
        self._footer.accept_button.setEnabled(checked)
