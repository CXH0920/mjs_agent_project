"""数据管理对话框：安全批量清空攻略和相性数据。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.business.maintenance.data_management_service import DataManagementService
from src.data.guide_manager import GuideManager
from src.data.synergy_manager import SynergyManager


class DataManagementDialog(QDialog):
    """提供带备份和二次确认的攻略、相性批量清空入口。"""

    data_cleared = Signal(bool, bool)

    def __init__(
        self,
        guide_manager: GuideManager,
        synergy_manager: SynergyManager,
        is_generation_busy: Callable[[], bool],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._guide_manager = guide_manager
        self._synergy_manager = synergy_manager
        self._is_generation_busy = is_generation_busy
        self._service = DataManagementService(guide_manager, synergy_manager)
        self._guide_checkbox: QCheckBox
        self._synergy_checkbox: QCheckBox
        self._count_label: QLabel
        self._clear_button: QPushButton

        self.setWindowTitle("数据管理")
        self.setMinimumWidth(420)
        self._setup_ui()
        self._refresh_counts()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        hint = QLabel("选择需要清空的数据。操作前会自动备份，清空后无法在界面中撤销。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._guide_checkbox = QCheckBox("清空武将攻略")
        self._synergy_checkbox = QCheckBox("清空武将相性")
        self._guide_checkbox.toggled.connect(self._update_clear_button)
        self._synergy_checkbox.toggled.connect(self._update_clear_button)
        layout.addWidget(self._guide_checkbox)
        layout.addWidget(self._synergy_checkbox)

        self._count_label = QLabel()
        layout.addWidget(self._count_label)

        actions = QHBoxLayout()
        actions.addStretch()
        self._clear_button = QPushButton("清空选中数据")
        self._clear_button.setStyleSheet("background-color: #c62828; color: white; padding: 6px 16px;")
        self._clear_button.clicked.connect(self._on_clear)
        actions.addWidget(self._clear_button)
        layout.addLayout(actions)

        close_layout = QHBoxLayout()
        close_layout.addStretch()
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        close_layout.addWidget(close_button)
        layout.addLayout(close_layout)

    def _refresh_counts(self) -> None:
        guide_count = len(self._guide_manager.list_guides())
        synergy_count = len(self._synergy_manager.list_synergies())
        self._count_label.setText(f"当前数据：攻略 {guide_count} 条；相性 {synergy_count} 条")
        self._update_clear_button()

    def _update_clear_button(self) -> None:
        self._clear_button.setEnabled(
            self._guide_checkbox.isChecked() or self._synergy_checkbox.isChecked()
        )

    def _on_clear(self) -> None:
        if self._is_generation_busy():
            QMessageBox.warning(self, "无法清空", "攻略或相性生成任务正在运行，请等待任务结束后再操作。")
            return

        clear_guides = self._guide_checkbox.isChecked()
        clear_synergies = self._synergy_checkbox.isChecked()
        selected = []
        if clear_guides:
            selected.append(f"攻略 {len(self._guide_manager.list_guides())} 条")
        if clear_synergies:
            selected.append(f"相性 {len(self._synergy_manager.list_synergies())} 条")
        text, accepted = QInputDialog.getText(
            self,
            "确认清空",
            f"将清空：{'；'.join(selected)}。\n操作前会自动备份。请输入“清空”确认：",
        )
        if not accepted or text.strip() != "清空":
            return

        try:
            result = self._service.clear_data(guides=clear_guides, synergies=clear_synergies)
        except Exception as error:
            QMessageBox.critical(self, "清空失败", f"无法清空数据：\n{error}")
            return

        self._guide_checkbox.setChecked(False)
        self._synergy_checkbox.setChecked(False)
        self._refresh_counts()
        self.data_cleared.emit(clear_guides, clear_synergies)
        backup_text = "\n".join(str(path) for path in result.backup_paths)
        QMessageBox.information(
            self,
            "清空完成",
            f"已清空攻略 {result.cleared_guides} 条；相性 {result.cleared_synergies} 条。\n"
            f"备份文件：\n{backup_text}",
        )
