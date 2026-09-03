"""
名将杀 Agent - 实战配队导入对话框

选择外部工具导出的 JSON（含 combos 字段），按 heroes.json 映射武将 ID
并解析 note 座次，写入 data/combos.json；报告未匹配/重复/校验失败等明细。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSettings, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from src.config.env import PROJECT_ROOT
from src.business.maintenance.combo_import_service import (
    DEFAULT_HEROES,
    DEFAULT_OUTPUT,
    run_import,
)
from src.ui.shared.style import ROLE_SECONDARY, set_ui_role
from src.ui.shared.widgets import DialogFooter, PageHeader

logger = logging.getLogger(__name__)

_NOTE_PREVIEW_LIMIT = 50

# 持有运行中的 worker，防止对话框销毁后 QThread 运行中被 GC 析构（同分类面板 #61）
_LIVE_WORKERS: set = set()


class _ImportWorker(QThread):
    """后台执行组合导入：数据量大时避免主线程冻结。"""

    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, source: Path, heroes_path: Path, output_path: Path, parent=None):
        super().__init__(parent)
        self._source = source
        self._heroes_path = heroes_path
        self._output_path = output_path

    def run(self) -> None:
        _LIVE_WORKERS.add(self)
        try:
            report = run_import(self._source, self._heroes_path, self._output_path)
        except Exception as error:
            logger.exception("实战配队导入失败")
            self.failed.emit(str(error))
        else:
            self.finished_ok.emit(report)
        finally:
            _LIVE_WORKERS.discard(self)


def _default_source_dir() -> Path:
    """文件选择起点：记忆上次使用目录，首次使用兜底项目 data/。"""
    settings = QSettings("MingJiangSha", "MJSAgent")
    last = str(settings.value("combos_import/last_dir", "") or "")
    if last and Path(last).is_dir():
        return Path(last)
    return PROJECT_ROOT / "data"


class CombosImportDialog(QDialog):
    """实战配队导入对话框

    确认后执行名称→ID 映射与座次解析的幂等导入；
    导入成功后发出 combos_imported(导入条数) 信号。
    """

    combos_imported = Signal(int)

    def __init__(
        self,
        heroes_path: Path = DEFAULT_HEROES,
        output_path: Path = DEFAULT_OUTPUT,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("导入实战配队")
        self.setMinimumSize(640, 480)
        self._heroes_path = Path(heroes_path)
        self._output_path = Path(output_path)
        self._worker: _ImportWorker | None = None
        self._setup_ui()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(
            "导入实战配队",
            "选择外部工具导出的 JSON（含 combos 字段），自动映射武将并解析座次",
        ))

        source_layout = QHBoxLayout()
        source_layout.addWidget(QLabel("导出文件:"))
        self._source_edit = QLineEdit()
        self._source_edit.setPlaceholderText(str(_default_source_dir() / "data.json"))
        source_layout.addWidget(self._source_edit, 1)
        self._browse_btn = QPushButton("浏览…")
        set_ui_role(self._browse_btn, ROLE_SECONDARY)
        self._browse_btn.clicked.connect(self._on_browse)
        source_layout.addWidget(self._browse_btn)
        layout.addLayout(source_layout)

        target_label = QLabel(
            f"输出: {self._output_path}\n武将映射: {self._heroes_path}"
        )
        target_label.setStyleSheet("color: gray; font-size: 12px;")
        layout.addWidget(target_label)

        self._report_browser = QTextBrowser()
        self._report_browser.setObjectName("combosImportReport")
        self._report_browser.setPlaceholderText("执行导入后此处显示报告……")
        layout.addWidget(self._report_browser, 1)

        self._footer = DialogFooter(accept_text="执行导入", cancel_text="关闭")
        self._footer.accepted.connect(self._on_accept)
        self._footer.rejected.connect(self.reject)
        layout.addWidget(self._footer)

    # ---------------------------------------------------------------
    # 交互
    # ---------------------------------------------------------------

    def _on_browse(self) -> None:
        start_dir = _default_source_dir()
        if start_dir.exists():
            start_dir = start_dir / "data.json"
        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择外部工具导出 JSON", str(start_dir), "JSON 文件 (*.json)"
        )
        if chosen:
            self._source_edit.setText(chosen)

    def _on_accept(self) -> None:
        source_text = self._source_edit.text().strip()
        if not source_text:
            QMessageBox.warning(self, "未选择文件", "请先选择外部工具导出的 JSON 文件。")
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._footer.set_busy(True, "导入中...")
        self._worker = _ImportWorker(Path(source_text), self._heroes_path, self._output_path)
        self._worker.finished_ok.connect(self._on_import_finished)
        self._worker.failed.connect(self._on_import_failed)
        self._worker.start()

    def _on_import_finished(self, report: dict) -> None:
        self._footer.set_busy(False)
        self._report_browser.setPlainText(self._format_report(report))
        if report["imported"]:
            self.combos_imported.emit(report["imported"])

    def _on_import_failed(self, error: str) -> None:
        self._footer.set_busy(False)
        self._report_browser.setPlainText(f"导入失败：{error}")
        QMessageBox.critical(self, "导入失败", f"无法完成导入：\n{error}")

    # ---------------------------------------------------------------
    # 报告
    # ---------------------------------------------------------------

    @staticmethod
    def _format_report(report: dict) -> str:
        seats = report["seat_stats"]
        lines = [
            f"导入完成：源 {report['total']} 条 → 写入 {report['imported']} 条",
            f"座次解析：成功 {seats['parsed']} + 无要求 {seats['none']}"
            f" + 部分 {seats['partial']} + 失败 {seats['unparsed']}",
        ]

        def append_block(title: str, items: list, render) -> None:
            if not items:
                return
            lines.append("")
            lines.append(f"⚠ {title} {len(items)} 条：")
            for item in items[:_NOTE_PREVIEW_LIMIT]:
                lines.append(f"  {render(item)}")
            if len(items) > _NOTE_PREVIEW_LIMIT:
                lines.append(f"  ……其余 {len(items) - _NOTE_PREVIEW_LIMIT} 条略")

        append_block(
            "手工记录保留（不在本次导出中）", report["manual_kept"],
            lambda i: f"{i['hero1']} + {i['hero2']} | {i['note'][:40]}",
        )
        append_block(
            "与手工记录冲突（已保留手工版本）", report["manual_collisions"],
            lambda i: f"源 #{i['index']} {i['hero1']} + {i['hero2']}",
        )
        append_block(
            "源中已不存在而移除", report["removed_stale"],
            lambda i: f"{i['hero1']} + {i['hero2']}",
        )
        append_block(
            "未匹配武将", report["unmatched"],
            lambda i: f"#{i['index']} {i['hero1']} + {i['hero2']}",
        )
        append_block(
            "重复配对（保留首条）", report["duplicates"],
            lambda i: f"#{i['index']} {i['hero1']} + {i['hero2']}",
        )
        append_block(
            "字段校验失败", report["invalid"],
            lambda i: f"#{i['index']} {i['hero1']} + {i['hero2']}: {i['error']}",
        )
        append_block(
            "座次需人工复核（按无座次导入）", report["seat_review"],
            lambda i: f"#{i['index']} {i['hero1']} + {i['hero2']} | {i['note'][:40]}",
        )
        append_block(
            "座次与 position 不一致（以 note 为准）", report["position_mismatch"],
            lambda i: f"#{i['index']} {i['hero1']} + {i['hero2']} | position={i['position']}",
        )
        return "\n".join(lines)
