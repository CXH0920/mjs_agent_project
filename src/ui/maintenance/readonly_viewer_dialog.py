# -*- coding: utf-8 -*-
"""只读文本查看对话框：头部元信息 + 全文正文 + 简易查找 + 关闭。

纯展示组件，不读文件——数据（元信息/正文/警示）由调用方准备，便于直接
单测。消费者：知识库维护的只读语料内容查看（P1-5）与执行日志历史弹窗
（P1-4）。主体用只读 QPlainTextEdit 而非 QTextBrowser：语料正文实测可达
408KB/6471 行，QPlainTextEdit 惰性布局适合长文档，且不走 render_markdown
的 20,000 字符渲染上限。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)
from src.ui.shared.style import ROLE_SECONDARY, set_ui_role
from src.ui.shared.widgets import DialogFooter, PageHeader


class ReadonlyViewerDialog(QDialog):
    """只读长文本查看器（查找框支持回绕到开头继续找）。"""

    def __init__(self, title: str, subtitle: str, meta_text: str, body: str,
                 *, note: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(860, 560)
        self.resize(920, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(PageHeader(title, subtitle))
        if meta_text:
            self.meta_label = QLabel(meta_text)
            self.meta_label.setObjectName("metaText")
            self.meta_label.setWordWrap(True)
            self.meta_label.setTextInteractionFlags(
                self.meta_label.textInteractionFlags()
                | Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(self.meta_label)
        if note:
            self.note_label = QLabel("⚠ " + note)
            self.note_label.setObjectName("metaText")
            self.note_label.setWordWrap(True)
            layout.addWidget(self.note_label)

        self.body = QPlainTextEdit()
        self.body.setObjectName("readonlyViewerBody")
        self.body.setReadOnly(True)
        self.body.setPlainText(body)
        layout.addWidget(self.body, 1)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("查找内容…（回车跳到下一处）")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self._find_next)
        search_row.addWidget(self.search_edit, 1)
        self._find_next_button = QPushButton("下一个")
        set_ui_role(self._find_next_button, ROLE_SECONDARY)
        self._find_next_button.clicked.connect(self._find_next)
        search_row.addWidget(self._find_next_button)
        self._find_status = QLabel("")
        self._find_status.setObjectName("scriptLogMeta")
        search_row.addWidget(self._find_status)
        layout.addLayout(search_row)

        footer = DialogFooter(accept_text="关闭", accept_role=ROLE_SECONDARY,
                              show_cancel=False)
        footer.accepted.connect(self.accept)
        layout.addWidget(footer)

    def _find_next(self) -> None:
        """向前查找；到尾未命中回绕到开头再找一次，仍无则提示无匹配。"""
        text = self.search_edit.text()
        if not text:
            self._find_status.setText("")
            return
        if self.body.find(text):
            self._find_status.setText("")
            return
        cursor = self.body.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.body.setTextCursor(cursor)
        self._find_status.setText(
            "" if self.body.find(text) else "无匹配")
