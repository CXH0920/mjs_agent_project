# -*- coding: utf-8 -*-
"""备份恢复对话框：列出 data/backups/ 自动快照，预览差异并一键恢复。

审查报告"备份恢复入口缺失"的补口：备份系统覆盖了全部数据写入路径，但
此前只有会手工翻目录的用户够得着。界面按"当前文件 + 最近快照"对照展示，
选中即预览键级差异；恢复前自动快照当前状态（双向可回退），恢复完成弹
变更摘要（新增/移除/变更条数）。武将/相性/攻略存在跨文件引用，单文件
恢复会造成引用失配并诱导失效关联修复删数据，故确认时默认提供整组恢复
到同一时间点。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from src.business.maintenance.backup_restore_service import (
    GROUP_KEYS,
    BackupEntry,
    RestoreTarget,
    default_targets,
    inspect_current,
    list_backups,
    preview_backup,
    restore_backup,
    restore_targets,
    select_group_entries,
)
from src.ui.shared.style import ROLE_DANGER
from src.ui.shared.widgets import DialogFooter, PageHeader

logger = logging.getLogger(__name__)

# 表格首列为当前文件伪行（仅供对照，不可恢复），其后为快照（新→旧）
_CURRENT_ROW_TEXT = "← 当前"


class BackupRestoreDialog(QDialog):
    """跨四类核心数据文件的备份浏览与恢复入口。"""

    restored = Signal(str)  # 恢复目标的 key（"heroes" 等），协调器按它分派刷新链

    def __init__(
        self,
        is_generation_busy: Callable[[], bool],
        parent=None,
        data_dir: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("从备份恢复")
        self.setMinimumSize(600, 520)
        self._is_busy = is_generation_busy
        self._targets = restore_targets(Path(data_dir)) if data_dir else default_targets()
        self._entries: list[BackupEntry | None] = []
        self._setup_ui()
        self._on_target_changed()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(
            "从备份恢复",
            "浏览数据文件的自动快照，预览差异后恢复。恢复前会自动快照当前状态，"
            "可随时再次恢复回来。",
        ))

        selector_layout = QHBoxLayout()
        selector_layout.addWidget(QLabel("选择数据文件："))
        self._target_combo = QComboBox()
        for target in self._targets.values():
            self._target_combo.addItem(f"{target.label} {target.file_path.name}", target.key)
        self._target_combo.currentIndexChanged.connect(self._on_target_changed)
        selector_layout.addWidget(self._target_combo, 1)
        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.clicked.connect(self._on_target_changed)
        selector_layout.addWidget(self._refresh_btn)
        layout.addLayout(selector_layout)

        self._source_label = QLabel()
        self._source_label.setStyleSheet("color: gray; font-size: 12px;")
        self._source_label.setWordWrap(True)
        layout.addWidget(self._source_label)

        self._table = QTableWidget(0, 3)
        self._table.setObjectName("backupList")
        self._table.setHorizontalHeaderLabels(["时间", "大小", "说明"])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table, 1)

        self._preview_label = QLabel()
        self._preview_label.setWordWrap(True)
        layout.addWidget(self._preview_label)

        self._warning_label = QLabel()
        self._warning_label.setWordWrap(True)
        self._warning_label.setStyleSheet("color: #b45309;")
        layout.addWidget(self._warning_label)

        self._footer = DialogFooter(
            accept_text="恢复选中备份",
            cancel_text="取消",
            accept_role=ROLE_DANGER,
        )
        self._restore_button = self._footer.accept_button
        self._footer.accepted.connect(self._on_restore)
        self._footer.rejected.connect(self.reject)
        layout.addWidget(self._footer)

    # ---------------------------------------------------------------
    # 列表与预览
    # ---------------------------------------------------------------

    def _current_target(self) -> RestoreTarget:
        key = self._target_combo.currentData()
        return self._targets[key]

    def _on_target_changed(self) -> None:
        target = self._current_target()
        self._table.setRowCount(0)
        self._entries: list[BackupEntry | None] = []
        self._source_label.setText(f"备份来源：{target.backup_source}")

        if target.file_path.exists():
            stat = target.file_path.stat()
            self._append_row(
                datetime.fromtimestamp(stat.st_mtime),
                stat.st_size,
                _CURRENT_ROW_TEXT,
                entry=None,
            )
        for entry in list_backups(target):
            self._append_row(
                entry.created_at, entry.size, _relative_time(entry.created_at), entry=entry
            )

        self._warning_label.setText(
            f"⚠ 恢复会覆盖当前 {target.file_path}。恢复前会自动把当前状态"
            f"再快照一份，且不会删除任何已有备份。"
        )
        if len(self._entries) <= (1 if target.file_path.exists() else 0):
            self._preview_label.setText(f"{target.label}数据暂无自动备份。")
        else:
            self._preview_label.setText("选择一份备份查看差异预览。")
        self._restore_button.setEnabled(False)

    def _append_row(
        self, created_at: datetime, size: int, note: str, entry: BackupEntry | None
    ) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        time_item = QTableWidgetItem(created_at.strftime("%Y-%m-%d %H:%M:%S"))
        time_item.setData(Qt.ItemDataRole.UserRole, entry)
        self._table.setItem(row, 0, time_item)
        self._table.setItem(row, 1, QTableWidgetItem(_human_size(size)))
        self._table.setItem(row, 2, QTableWidgetItem(note))
        self._entries.append(entry)

    def _on_selection_changed(self) -> None:
        row = self._table.currentRow()
        target = self._current_target()
        if row < 0 or row >= len(self._entries):
            self._restore_button.setEnabled(False)
            return
        entry = self._entries[row]
        if entry is None:  # 当前文件伪行：只展示现状，无恢复语义
            current = inspect_current(target)
            if current is None:
                self._preview_label.setText(
                    f"⚠ 当前 {target.file_path.name} 无法解析，建议从下方备份恢复。"
                )
            else:
                count, invalid = current
                hint = f"（另有 {invalid} 条坏记录）" if invalid else ""
                self._preview_label.setText(f"当前：{target.label} {count} {target.unit}{hint}。")
            self._restore_button.setEnabled(False)
            return

        preview = preview_backup(target, entry)
        if not preview.valid:
            reason = (
                f"含 {preview.invalid_count} 条坏记录" if preview.invalid_count else "文件已损坏"
            )
            self._preview_label.setText(f"⚠ 该备份{reason}，无法恢复。")
            self._restore_button.setEnabled(False)
            return
        if preview.added == preview.removed == preview.changed == 0:
            diff_text = "与当前完全一致"
        else:
            diff_text = (
                f"与当前相比 +{preview.added} / -{preview.removed} / 改 {preview.changed} 条"
            )
        self._preview_label.setText(
            f"预览：{target.label} {preview.record_count} {target.unit} · {diff_text}"
        )
        self._restore_button.setEnabled(True)

    # ---------------------------------------------------------------
    # 恢复
    # ---------------------------------------------------------------

    def _on_restore(self) -> None:
        target = self._current_target()
        if self._is_busy():
            QMessageBox.warning(
                self, "无法恢复", "攻略或相性生成任务正在运行，请等待任务结束后再操作。"
            )
            return
        row = self._table.currentRow()
        if row < 0 or row >= len(self._entries):
            return
        entry = self._entries[row]
        if entry is None:
            return

        choice, group_entries = self._confirm_restore(target, entry)
        if choice is None:
            return

        self._footer.set_busy(True, "恢复中...")
        if choice == "single":
            try:
                result = restore_backup(target, entry)
            except Exception as error:
                logger.exception("备份恢复失败")
                self._footer.set_busy(False)
                QMessageBox.critical(
                    self, "恢复失败",
                    f"无法完成恢复：\n{error}\n\n"
                    "当前文件未被修改（恢复采用原子写入，失败不落盘）。",
                )
                return
            self._footer.set_busy(False)
            QMessageBox.information(self, "恢复完成", self._summary_text(target, result))
        else:
            if not self._restore_group(entry, group_entries):
                return  # 整组全部失败，无任何文件变化，不必刷新
        self.restored.emit(target.key)
        self._on_target_changed()  # 当前文件已变，重载对照列表

    def _restore_group(
        self, anchor: BackupEntry,
        group_entries: dict[str, BackupEntry | None],
    ) -> bool:
        """按确认框选定的整组计划逐文件恢复；单文件失败不回滚其余文件。

        每个文件恢复前各自快照（双向可回退），失败文件保持原样；部分失败
        时在摘要中如实列出成功与失败清单。返回是否有文件实际被恢复。
        """
        changed_lines: list[str] = []
        skipped_lines: list[str] = []
        failures: list[str] = []
        for key, planned in group_entries.items():
            group_target = self._targets[key]
            if planned is None:
                skipped_lines.append(
                    f"{group_target.label}：保持当前状态（该时间点之前无备份）"
                )
                continue
            try:
                result = restore_backup(group_target, planned)
            except Exception as error:
                logger.exception("整组恢复中 %s 失败", key)
                failures.append(f"{group_target.label}：{error}")
                continue
            changed_lines.append(self._change_line(group_target, result.preview))
        self._footer.set_busy(False)

        if failures and not changed_lines:
            QMessageBox.critical(
                self, "恢复失败",
                "整组恢复全部失败，各文件均未被修改（恢复采用原子写入）：\n"
                + "\n".join(failures),
            )
            return False
        parts = [f"已整组恢复到 {anchor.created_at:%Y-%m-%d %H:%M:%S} 时间点。"]
        parts.extend(changed_lines)
        parts.extend(skipped_lines)
        if failures:
            parts.append("以下文件恢复失败（文件未被修改）：\n" + "\n".join(failures))
            parts.append("已恢复的文件均另存了恢复前快照，可再次打开本对话框重试或回退。")
            QMessageBox.warning(self, "部分恢复失败", "\n".join(parts))
        else:
            parts.append("每个已恢复文件在恢复前的状态均已另存为备份，可再次打开本对话框回退。")
            QMessageBox.information(self, "恢复完成", "\n".join(parts))
        return True

    def _confirm_restore(
        self, target: RestoreTarget, entry: BackupEntry
    ) -> tuple[str | None, dict[str, BackupEntry | None] | None]:
        """确认恢复方式，返回 ("group"|"single"|None, 整组选择结果)。

        核心三文件存在跨文件引用：单文件恢复会引发失效关联修复提示（按
        提示修复即删除引用记录），故默认提供整组恢复到同一时间点。
        """
        if target.key not in GROUP_KEYS:
            reply = QMessageBox.question(
                self,
                "确认恢复",
                f"将把 {target.label}数据（{target.file_path.name}）恢复到 "
                f"{entry.created_at:%Y-%m-%d %H:%M:%S} 的备份。\n\n"
                "当前文件会先自动快照，之后仍可从此对话框恢复回来。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            return ("single" if reply == QMessageBox.StandardButton.Yes else None), None

        group = select_group_entries(self._targets, target.key, entry)
        plan_lines = [
            f"{self._targets[key].label} → {planned.created_at:%m-%d %H:%M} 的备份"
            for key, planned in group.items()
            if planned is not None and key != target.key
        ]
        skipped_lines = [
            f"{self._targets[key].label}：该时间点之前无备份，将保持当前状态"
            for key, planned in group.items()
            if planned is None
        ]
        box = QMessageBox(self)
        box.setWindowTitle("确认恢复")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f"选中：{target.label} {entry.created_at:%Y-%m-%d %H:%M:%S} 的备份。\n\n"
            "【整组恢复（推荐）】武将/相性/攻略同时恢复到该时间点，保持引用一致：\n"
            + ("\n".join(plan_lines) if plan_lines else "（组内其他文件该时间点前无备份）")
            + ("\n" + "\n".join(skipped_lines) if skipped_lines else "")
            + "\n\n【仅恢复该文件】会使相性/攻略与武将的引用关系失配，恢复后若按"
            "“失效关联修复”提示操作，将删除引用缺失武将的攻略与相性记录。"
        )
        group_button = box.addButton("整组恢复（推荐）", QMessageBox.ButtonRole.AcceptRole)
        single_button = box.addButton("仅恢复该文件", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is group_button:
            return "group", group
        if clicked is single_button:
            return "single", None
        return None, None

    @staticmethod
    def _change_line(target: RestoreTarget, preview) -> str:
        """单文件一行式变更说明（单文件与整组摘要共用）。"""
        return (
            f"{target.label}：{preview.current_count} → {preview.record_count} {target.unit}"
            f"（新增 {preview.added} / 移除 {preview.removed} / 变更 {preview.changed}）"
        )

    @classmethod
    def _summary_text(cls, target: RestoreTarget, result) -> str:
        """恢复完成后的变更摘要（用户确认的自动摘要需求）。"""
        preview = result.preview
        lines = [
            f"已将 {target.file_path.name} 恢复到 "
            f"{preview.entry.created_at:%Y-%m-%d %H:%M:%S} 的备份。",
            cls._change_line(target, preview),
        ]
        if result.snapshot_path is not None:
            lines.append(
                f"恢复前的状态已另存为备份（{result.snapshot_path.name}），"
                "可再次打开本对话框回退。"
            )
        else:
            lines.append("恢复前目标文件不存在，未产生新快照。")
        return "\n".join(lines)


def _relative_time(moment: datetime) -> str:
    """快照时间的相对描述（“（9 天前）”），“刚刚”与新快照对齐。"""
    seconds = (datetime.now() - moment).total_seconds()
    if seconds < 60:
        return "（刚刚）"
    if seconds < 3600:
        return f"（{int(seconds // 60)} 分钟前）"
    if seconds < 86400:
        return f"（{int(seconds // 3600)} 小时前）"
    return f"（{int(seconds // 86400)} 天前）"


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} GB"
