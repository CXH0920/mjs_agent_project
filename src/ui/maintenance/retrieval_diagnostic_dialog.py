# -*- coding: utf-8 -*-
"""检索诊断抽屉：输入查询 → 后台检索 → 表格呈现每条候选的得分与剔除原因。

定位语料/索引变动前后的检索行为差异（G5）：同一 query 各跑一次，对照
trace 中新增/丢失的保留块与被各丢弃点剔除的块。只读工具，不改任何数据。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from src.business.rag.retrieval_diagnostic import DiagnosticWorker
from src.ui.shared.style import ROLE_PRIMARY, set_ui_role

# trace.dropped 的枚举值 → 展示词（与 src/rag/retriever.py 的插桩点一一对应）
DROP_LABELS = {
    None: "保留",
    "below_threshold": "向量分低于阈值",
    "not_current": "非当前版本",
    "hero_filter": "武将过滤",
    "max_keyword_only": "纯关键词超限",
    "kind_quota": "类型配额",
}

_COLUMNS = ["block_id", "类型", "武将", "得分", "来源", "rrf",
            "当前版", "as_of", "剔除原因"]

_TOP_K_CHOICES = [5, 8, 12, 20]


class RetrievalDiagnosticDialog(QDialog):
    """检索诊断：查询框 + 候选去向表 + 状态栏（backend / 耗时 / 条数）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("检索诊断")
        self.resize(880, 560)
        self._worker: DiagnosticWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        query_row = QHBoxLayout()
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("输入查询文本，回车或点击「检索」")
        self.query_edit.returnPressed.connect(self._run_query)
        query_row.addWidget(self.query_edit, 1)
        self.top_k_box = QComboBox()
        for value in _TOP_K_CHOICES:
            self.top_k_box.addItem(str(value), value)
        self.top_k_box.setCurrentIndex(_TOP_K_CHOICES.index(12))
        query_row.addWidget(QLabel("top_k"))
        query_row.addWidget(self.top_k_box)
        self.run_button = QPushButton("检索")
        set_ui_role(self.run_button, ROLE_PRIMARY)
        self.run_button.clicked.connect(self._run_query)
        query_row.addWidget(self.run_button)
        layout.addLayout(query_row)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

        self.status_label = QLabel("输入查询开始诊断（首次检索需加载编码器，稍候）")
        self.status_label.setObjectName("scriptLogMeta")
        layout.addWidget(self.status_label)

    # ---------------------------------------------------------------
    # 检索执行（后台线程）与结果渲染
    # ---------------------------------------------------------------
    def _run_query(self) -> None:
        query = self.query_edit.text().strip()
        if not query or self._worker is not None:
            return
        self.run_button.setEnabled(False)
        self.status_label.setText("检索中……（首次运行需加载编码器与语料）")
        top_k = self.top_k_box.currentData()
        self._worker = DiagnosticWorker(query, top_k=top_k)
        self._worker.result_ready.connect(self._on_result)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_worker_finished)
        # 自回收：dialog 先销毁（断开全部以其为 receiver 的连接）时，
        # finished→deleteLater 仍保证 worker 释放
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_result(self, result: dict) -> None:
        rows = list(result.get("trace") or [])
        # sortingEnabled 下逐 setItem 会即时重排并错位后续按坐标写入
        # （Qt 文档明示须填充期间禁用排序），填完再恢复点击列头排序能力
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        kept = 0
        for row_idx, entry in enumerate(rows):
            dropped = entry.get("dropped")
            kept += dropped is None
            # below_threshold 候选在 RRF 融合前就被丢弃，无融合分——
            # 显示占位符而不是误导性的 0.00000
            rrf_text = "—" if dropped == "below_threshold" \
                else f"{entry.get('rrf', 0.0):.5f}"
            values = [
                str(entry.get("block_id", "")),
                str(entry.get("kind", "")),
                str(entry.get("hero", "")),
                f"{entry.get('score', 0.0):.3f}",
                str(entry.get("source", "")),
                rrf_text,
                "是" if entry.get("is_current", "true") != "false" else "否",
                str(entry.get("as_of", "")),
                DROP_LABELS.get(dropped, str(dropped)),
            ]
            for col_idx, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col_idx == 3:
                    item.setData(Qt.ItemDataRole.EditRole, float(values[3]))
                elif col_idx == 5 and rrf_text != "—":
                    item.setData(Qt.ItemDataRole.EditRole, float(rrf_text))
                self.table.setItem(row_idx, col_idx, item)
        self.table.setSortingEnabled(True)
        dropped_total = len(rows) - kept
        self.status_label.setText(
            f"backend={result.get('backend', '?')}  "
            f"耗时 {result.get('elapsed_ms', 0)}ms  "
            f"保留 {kept} 条 / 丢弃 {dropped_total} 条")

    def _on_failed(self, message: str) -> None:
        self.status_label.setText(f"检索失败：{message}")

    def _on_worker_finished(self) -> None:
        # 释放仅靠引用；回收统一走 finished→deleteLater（见 _run_query）
        self._worker = None
        self.run_button.setEnabled(True)

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt 命名
        # 只摘引用、不 deleteLater：仍在运行的线程由 LIVE_WORKERS 兜底持有，
        # 结束后经 finished→deleteLater 自回收。此处若 wait 超时后强删，
        # running QThread 析构是 qFatal（Qt6 实证），dialog 先走会拉崩进程
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.wait(2000)
        super().closeEvent(event)
