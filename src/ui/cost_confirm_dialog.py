"""
名将杀 Agent - 成本确认对话框

展示攻略批量生成的成本估算结果，供用户确认是否执行。
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class CostConfirmDialog(QDialog):
    """成本确认对话框

    展示需要生成的攻略数、预估 Token 消耗和费用，
    用户点击确定后继续执行，点击取消则放弃。
    """

    def __init__(self, estimation: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("攻略生成成本估算")
        self.setMinimumWidth(420)
        self._estimation = estimation
        self._setup_ui()

    def _setup_ui(self) -> None:
        """构建对话框界面"""
        layout = QVBoxLayout(self)

        mode_text = {"all": "全量获取", "incremental": "增量获取", "specific": "指定获取"}
        m = mode_text.get(self._estimation.get("mode", ""), "未知")

        info_lines = [
            f"模式: {m}",
            f"需要生成的攻略数: {self._estimation.get('items', 0)} 个",
            f"预估输入 Token: {self._estimation.get('estimated_input_tokens', 0):,}",
            f"预估输出 Token: {self._estimation.get('estimated_output_tokens', 0):,}",
            f"合计 Token: {self._estimation.get('estimated_tokens', 0):,}",
            f"预估费用: CNY {self._estimation.get('estimated_cost_cny', 0):.4f}",
        ]

        for line in info_lines:
            label = QLabel(line)
            label.setStyleSheet("font-size: 14px; padding: 2px 0;")
            layout.addWidget(label)

        # 消息提示
        msg = self._estimation.get("message", "")
        if msg:
            msg_label = QLabel(msg)
            msg_label.setStyleSheet("color: gray; font-size: 13px; padding: 4px 0;")
            layout.addWidget(msg_label)

        layout.addSpacing(10)

        # 确认 / 取消 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        ok_btn = QPushButton("确定执行")
        ok_btn.setStyleSheet("padding: 6px 24px;")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet("padding: 6px 24px;")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
