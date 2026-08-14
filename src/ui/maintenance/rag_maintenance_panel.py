# -*- coding: utf-8 -*-
"""知识库维护工作台（主导航第 4 页）。

功能：
- 变更预览：对比权威源（data/、docs/）与 data/rag_corpus 语料的修改时间，标记 8 个语料任务状态；
- 审计提示：未归类武将、专属牌引用未知武将等人工维护清单；
- 一键执行：本地运行 scripts/maintain_rag.py 重建语料/索引（不再依赖外部 mjs 仓库），实时输出日志。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (
    QFrame,
    QHeaderView,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.config.env import PROJECT_ROOT
from src.ui.shared.style import ROLE_PRIMARY, ROLE_SECONDARY, TONE_SUCCESS, TONE_WARNING
from src.ui.shared.widgets import NoticeBanner, PageActionBar

# 语料任务：名称 -> (源文件, 输出语料文件)
TASK_DEFS: list[tuple[str, list[str], list[str]]] = [
    ("武将语料", ["data/heroes.json", "data/cards.json"], ["武将RAG语料.json"]),
    ("卡牌语料", ["data/cards.json"], ["卡牌RAG语料.json"]),
    ("点数花色语料", ["data/mjs卡牌点数.xlsx"], ["卡牌点数花色语料.json"]),
    ("装备属性语料", ["data/cards.json"], ["装备属性语料.json"]),
    ("加强削弱语料", ["data/cards.json", "data/card_annotations.json"], ["加强削弱语料.json"]),
    ("元规则/术语/FAQ", ["docs/元规则整理-完整版.md"],
     ["元规则RAG语料-章节块.json", "术语表.json", "FAQ裁定块.json"]),
    ("特殊机制语料", ["data/special_cards.json"], ["特殊机制语料.json"]),
    ("武将分类语料", ["data/hero_classification.json", "data/heroes.json"], ["武将分类语料.json"]),
]

CORPUS_DIR = "data/rag_corpus"
PYTHON = sys.executable


def task_states(root: Path) -> list[dict]:
    """计算各语料任务状态；供 UI 展示与测试。"""
    rows = []
    for name, sources, outputs in TASK_DEFS:
        source_mtime = 0.0
        missing_sources = []
        for rel in sources:
            path = root / rel
            if path.exists():
                source_mtime = max(source_mtime, path.stat().st_mtime)
            else:
                missing_sources.append(rel)
        output_mtimes = []
        for rel in outputs:
            path = root / CORPUS_DIR / rel
            output_mtimes.append(path.stat().st_mtime if path.exists() else 0.0)
        if missing_sources:
            status = "缺源"
        elif not output_mtimes or any(t == 0.0 for t in output_mtimes):
            status = "待重建"
        elif source_mtime > max(output_mtimes) + 1:
            status = "待重建"
        else:
            status = "最新"
        count = None
        if output_mtimes and output_mtimes[0] > 0:
            try:
                data = json.loads((root / CORPUS_DIR / outputs[0]).read_text(encoding="utf-8"))
                count = len(data) if isinstance(data, list) else None
            except (OSError, json.JSONDecodeError):
                count = None
        rows.append({"name": name, "status": status, "count": count,
                     "sources": sources, "outputs": outputs})
    return rows


def audit_summary(root: Path) -> list[str]:
    """返回人工维护提示清单（空列表表示无问题）。"""
    issues: list[str] = []
    heroes_path = root / "data" / "heroes.json"
    classification_path = root / "data" / "hero_classification.json"
    special_path = root / "data" / "special_cards.json"
    try:
        heroes = json.loads(heroes_path.read_text(encoding="utf-8"))
        hero_names = {item.get("name") for item in heroes}
    except (OSError, json.JSONDecodeError):
        hero_names = set()
    try:
        classification = json.loads(classification_path.read_text(encoding="utf-8"))
        classified = set(classification.get("hero_categories", {}))
        unclassified = sorted(hero_names - classified)
        if unclassified:
            issues.append(f"未归类武将 {len(unclassified)} 人（请补充 data/hero_classification.json）")
    except (OSError, json.JSONDecodeError):
        issues.append("data/hero_classification.json 缺失或无法解析")
    try:
        specials = json.loads(special_path.read_text(encoding="utf-8"))
        unknown = sorted({
            item.get("hero", "") for item in specials
            if item.get("hero") and item["hero"] != "通用" and item["hero"] not in hero_names
        })
        if unknown:
            issues.append(f"专属牌引用未知武将 {len(unknown)} 人：{'、'.join(unknown[:8])}")
    except (OSError, json.JSONDecodeError):
        issues.append("data/special_cards.json 缺失或无法解析")
    return issues


class RagMaintenancePanel(QWidget):
    """知识库维护工作台。"""

    def __init__(self, root: Path = PROJECT_ROOT, parent=None):
        super().__init__(parent)
        self._root = root
        self._proc: QProcess | None = None
        self._setup_ui()
        self.refresh()

    def _setup_ui(self) -> None:
        self.setObjectName("ragMaintenancePanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self._action_bar = PageActionBar("正在检查……", self)
        self._status_label = self._action_bar.status_label
        self._refresh_button = QPushButton("刷新状态")
        self._refresh_button.clicked.connect(self.refresh)
        self._action_bar.add_action(self._refresh_button, ROLE_SECONDARY)
        self._hero_button = QPushButton("重建武将语料")
        self._hero_button.clicked.connect(lambda: self._run(["--force", "--only", "武将"]))
        self._action_bar.add_action(self._hero_button, ROLE_SECONDARY)
        self._corpus_button = QPushButton("重建全部语料")
        self._corpus_button.clicked.connect(lambda: self._run(["--force"]))
        self._action_bar.add_action(self._corpus_button, ROLE_SECONDARY)
        self._index_button = QPushButton("重建语料+索引")
        self._index_button.clicked.connect(lambda: self._run(["--force", "--build-index"]))
        self._action_bar.add_action(self._index_button, ROLE_PRIMARY)
        layout.addWidget(self._action_bar)

        self._table_surface = QFrame()
        self._table_surface.setObjectName("ragTableSurface")
        table_layout = QVBoxLayout(self._table_surface)
        table_layout.setContentsMargins(10, 10, 10, 10)
        table_layout.setSpacing(0)
        self._table = QTableWidget(0, 4)
        self._table.setObjectName("ragTaskTable")
        self._table.setHorizontalHeaderLabels(["语料任务", "状态", "输出块数", "数据源"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        table_layout.addWidget(self._table)
        layout.addWidget(self._table_surface, 1)

        self._audit_banner = NoticeBanner("人工维护检查通过", "", TONE_SUCCESS, self)
        self._audit_label = self._audit_banner.title_label
        layout.addWidget(self._audit_banner)

        self._log_surface = QFrame()
        self._log_surface.setObjectName("ragLogSurface")
        log_layout = QVBoxLayout(self._log_surface)
        log_layout.setContentsMargins(10, 10, 10, 10)
        log_layout.setSpacing(0)
        self._log = QPlainTextEdit()
        self._log.setObjectName("ragMaintenanceLog")
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("执行日志将显示在这里……")
        self._log.setMaximumBlockCount(2000)
        log_layout.addWidget(self._log)
        layout.addWidget(self._log_surface, 2)

    def refresh(self) -> None:
        rows = task_states(self._root)
        stale = [row["name"] for row in rows if row["status"] == "待重建"]
        self._table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            name_item = QTableWidgetItem(row["name"])
            mark = {"最新": "✓ ", "待重建": "⚠ ", "缺源": "✗ "}.get(row["status"], "")
            status_item = QTableWidgetItem(mark + row["status"])
            count_item = QTableWidgetItem(str(row["count"]) if row["count"] is not None else "-")
            source_item = QTableWidgetItem("、".join(row["sources"]))
            self._table.setItem(index, 0, name_item)
            self._table.setItem(index, 1, status_item)
            self._table.setItem(index, 2, count_item)
            self._table.setItem(index, 3, source_item)
        if stale:
            self._action_bar.set_status(f"有 {len(stale)} 个语料任务待重建：{'、'.join(stale)}", TONE_WARNING)
        else:
            self._action_bar.set_status("所有语料与数据源一致", TONE_SUCCESS)
        issues = audit_summary(self._root)
        if issues:
            self._audit_banner.set_tone(TONE_WARNING)
            self._audit_label.setText("人工维护提示")
            self._audit_banner.message_label.setText("\n".join(f"· {item}" for item in issues))
            self._audit_banner.message_label.setVisible(True)
        else:
            self._audit_banner.set_tone(TONE_SUCCESS)
            self._audit_label.setText("人工维护检查通过")
            self._audit_banner.message_label.setText("")
            self._audit_banner.message_label.setVisible(False)

    # ---------------------------------------------------------------
    # 本地执行（QProcess）
    # ---------------------------------------------------------------
    def _run(self, args: list[str]) -> None:
        if self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, "正在执行", "已有维护任务运行中，请等待完成。")
            return
        script = self._root / "scripts" / "maintain_rag.py"
        if not script.exists():
            QMessageBox.critical(self, "脚本缺失", f"未找到 {script}")
            return
        self._set_busy(True)
        self._log.clear()
        self._log.appendPlainText("$ python scripts/maintain_rag.py " + " ".join(args))
        proc = QProcess(self)
        proc.setWorkingDirectory(str(self._root))
        proc.readyReadStandardOutput.connect(lambda: self._append_log(proc.readAllStandardOutput()))
        proc.readyReadStandardError.connect(lambda: self._append_log(proc.readAllStandardError()))
        proc.finished.connect(lambda code, status: self._on_finished(code, status))
        proc.start(PYTHON, [str(script)] + args)
        self._proc = proc

    def _append_log(self, data: bytes) -> None:
        text = bytes(data).decode("utf-8", errors="replace")
        self._log.appendPlainText(text.rstrip("\n"))

    def _on_finished(self, code: int, _status) -> None:
        self._set_busy(False)
        if code == 0:
            self._log.appendPlainText("\n✔ 执行完成")
        else:
            self._log.appendPlainText(f"\n✘ 执行失败（退出码 {code}）")
        self._proc = None
        self.refresh()

    def _set_busy(self, busy: bool) -> None:
        for button in (self._refresh_button, self._hero_button, self._corpus_button, self._index_button):
            button.setEnabled(not busy)