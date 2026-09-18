"""白名单配置对话框：错法观察、用户层白名单的查看与维护。

数据来源：
- data/ocr_name_pending_stats.json —— 识别未决错法的频次与用户人工确认答案
  （由 pending_stats 双事件流积累）；
- data/ocr_confusion_overrides.json —— 用户层白名单（本界面写入）；
- 基线表 SAFE_SUBSTITUTION_WHITELIST 随版本只读展示。

写入用户层对后调用 CaptureService.reset_ocr_recognizer_cache()，下次识别即
读到新表，无需重启应用。所有新增均先经 find_whitelist_conflicts 静态冲突
检查，与词表构成「等长差一字高危对」的会被拒绝。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from src.business.recognition.pending_stats import STATS_PATH
from src.config.env import PROJECT_ROOT
from src.ocr.character_similarity import (
    CharacterSimilarityService,
    find_whitelist_conflicts,
)
from src.ui.shared.widgets import PageHeader

logger = logging.getLogger(__name__)

_OVERRIDES_PATH = PROJECT_ROOT / "data" / "ocr_confusion_overrides.json"
_CONFIRMED = {"exact", "unique_prefix", "unique_similarity", "multi_similarity", "slot_unique", "manual"}
# 分类排序权重：A+（已有人工确认答案）> A（候选唯一）> B（候选不唯一）> C（截断）
_CATEGORY_ORDER = {"A+": 0, "A": 1, "B": 2, "C": 3, "D": 4}


def load_overrides(path: Path | None = None) -> dict[str, str]:
    """读取用户层白名单；文件缺失或损坏返回空表（识别侧同样降级）。"""
    path = path or _OVERRIDES_PATH
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        pairs = document.get("pairs")
        if not isinstance(pairs, dict):
            return {}
        return {
            str(source): str(target)
            for source, target in pairs.items()
            if isinstance(source, str) and isinstance(target, str)
        }
    except (OSError, ValueError, AttributeError) as exc:
        logger.warning("用户层白名单读取失败: %s", exc)
        return {}


def save_overrides(pairs: dict[str, str], path: Path | None = None) -> None:
    path = path or _OVERRIDES_PATH
    document = {"version": 1, "pairs": dict(sorted(pairs.items()))}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(document, file, ensure_ascii=False, indent=1)
        file.write("\n")
    temporary.replace(path)


def load_pending_entries(path: Path | None = None) -> dict[str, dict]:
    path = path or STATS_PATH
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        entries = document.get("entries")
        return entries if isinstance(entries, dict) else {}
    except (OSError, ValueError, AttributeError) as exc:
        logger.warning("错法频次读取失败: %s", exc)
        return {}


def classify_entry(raw_name: str, entry: dict) -> tuple[str, str, str]:
    """把一条频次记录分类为 A+/A/B/C，并给出建议的（错字, 正字）或空串。

    返回 (分类, 建议错字, 建议正字)；无建议时后两者为空。
    """
    confirmed = str(entry.get("confirmed", "")).strip()
    candidates = [str(c) for c in (entry.get("candidates") or [])]
    reference = confirmed or (candidates[0] if len(candidates) == 1 else "")
    if reference and len(reference) == len(raw_name):
        diffs = [(a, b) for a, b in zip(raw_name, reference, strict=True) if a != b]
        if len(diffs) == 1:
            category = "A+" if confirmed else "A"
            return category, diffs[0][0], diffs[0][1]
    if entry.get("is_truncation"):
        return "C", "", ""
    return "B", "", ""


class WhitelistConfigDialog(QDialog):
    """白名单配置：错法观察清单 + 用户层白名单维护。"""

    def __init__(self, hero_names: list[str], reset_ocr_cache, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("白名单配置")
        self.setMinimumWidth(720)
        self._hero_names = hero_names
        self._reset_ocr_cache = reset_ocr_cache
        self._overrides = load_overrides()
        self._selected_raw = ""
        self._selected_source = ""
        self._selected_suggestion = ("", "")

        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(
            "白名单配置",
            "识别中反复读错的字法沉淀为确定性纠错对；写入后立即对后续识别生效。",
        ))

        layout.addWidget(QLabel("错法观察（识别中无法自动确认的读数，按优先级排序）"))
        self._pending_table = QTableWidget(0, 5)
        self._pending_table.setHorizontalHeaderLabels(
            ["错法读数", "卡住次数", "你已确认", "候选", "分类/建议"],
        )
        self._pending_table.horizontalHeader().setStretchLastSection(True)
        self._pending_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._pending_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._pending_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._pending_table.itemSelectionChanged.connect(self._on_pending_selected)
        layout.addWidget(self._pending_table)

        pair_row = QHBoxLayout()
        pair_row.addWidget(QLabel("错字"))
        self._source_edit = QLineEdit()
        self._source_edit.setMaxLength(1)
        self._source_edit.setFixedWidth(40)
        pair_row.addWidget(self._source_edit)
        pair_row.addWidget(QLabel("正字"))
        self._target_edit = QLineEdit()
        self._target_edit.setMaxLength(1)
        self._target_edit.setFixedWidth(40)
        pair_row.addWidget(self._target_edit)
        self._add_button = QPushButton("加入白名单")
        self._add_button.clicked.connect(self._add_pair)
        pair_row.addWidget(self._add_button)
        pair_row.addStretch()
        layout.addLayout(pair_row)

        layout.addWidget(QLabel("当前用户层白名单（基线表随版本只读）"))
        self._overrides_table = QTableWidget(0, 2)
        self._overrides_table.setHorizontalHeaderLabels(["错字", "正字"])
        self._overrides_table.horizontalHeader().setStretchLastSection(True)
        self._overrides_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._overrides_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._overrides_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._overrides_table)

        button_row = QHBoxLayout()
        remove_button = QPushButton("删除选中用户层条目")
        remove_button.clicked.connect(self._remove_selected_override)
        button_row.addWidget(remove_button)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh)
        button_row.addWidget(refresh_button)
        button_row.addStretch()
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        self.refresh()

    # ── 数据加载与展示 ────────────────────────────────────────────────

    def refresh(self) -> None:
        self._overrides = load_overrides()
        self._reload_pending_table()
        self._reload_overrides_table()

    def _reload_pending_table(self) -> None:
        entries = load_pending_entries()
        classified = []
        for raw_name, entry in entries.items():
            category, source, target = classify_entry(raw_name, entry)
            classified.append((_CATEGORY_ORDER.get(category, 9), -int(entry.get("count", 0)),
                               raw_name, entry, category, source, target))
        classified.sort(key=lambda item: (item[0], item[1], item[2]))

        self._pending_table.setRowCount(len(classified))
        for row, (_order, _neg, raw_name, entry, category, source, target) in enumerate(classified):
            confirmed = str(entry.get("confirmed", ""))
            confirmed_count = int(entry.get("confirmed_count", 0))
            confirmed_text = f"{confirmed}（{confirmed_count} 次）" if confirmed else "—"
            suggestion = f"建议 {source}={target}" if source and target else {
                "A+": "整名差异，请人工判断", "B": "候选不唯一，请人工判断",
                "C": "截断读数，不建议补对",
            }.get(category, "")
            values = [
                raw_name, str(entry.get("count", 0)), confirmed_text,
                " / ".join(str(c) for c in (entry.get("candidates") or [])),
                f"{category}  {suggestion}".strip(),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, (raw_name, source, target, category))
                self._pending_table.setItem(row, column, item)

    def _reload_overrides_table(self) -> None:
        rows = sorted(self._overrides.items())
        self._overrides_table.setRowCount(len(rows))
        for row, (source, target) in enumerate(rows):
            self._overrides_table.setItem(row, 0, QTableWidgetItem(source))
            self._overrides_table.setItem(row, 1, QTableWidgetItem(target))

    # ── 操作 ─────────────────────────────────────────────────────────

    def _on_pending_selected(self) -> None:
        selected = self._pending_table.selectedItems()
        if not selected:
            return
        data = selected[0].data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        raw_name, source, target, category = data
        self._selected_raw = raw_name
        self._selected_suggestion = (source, target)
        # 仅 A+/A 类预填建议对；B/C 由用户自行填写（或不允许）
        self._source_edit.setText(source if category in ("A+", "A") else "")
        self._target_edit.setText(target if category in ("A+", "A") else "")

    def _add_pair(self) -> None:
        source = self._source_edit.text().strip()
        target = self._target_edit.text().strip()
        if len(source) != 1 or len(target) != 1 or source == target:
            QMessageBox.warning(self, "白名单配置", "请填写单个汉字，且错字与正字不同。")
            return
        merged = dict(CharacterSimilarityService.SAFE_SUBSTITUTION_WHITELIST)
        merged.update(self._overrides)
        merged[source] = target
        conflicts = find_whitelist_conflicts(self._hero_names, merged)
        if conflicts:
            detail = "；".join(f"{a} ↔ {b}（{c}）" for a, b, c in conflicts)
            QMessageBox.warning(
                self, "无法加入",
                f"该错字对会与词表中以下武将名构成确定性混淆，已被拒绝：\n{detail}",
            )
            return
        self._overrides[source] = target
        save_overrides(self._overrides)
        self._reset_ocr_cache()
        self.refresh()
        QMessageBox.information(self, "白名单配置", f"已加入 {source}→{target}，对后续识别立即生效。")

    def _remove_selected_override(self) -> None:
        selected = self._overrides_table.selectedItems()
        if not selected:
            return
        source = self._overrides_table.item(selected[0].row(), 0).text()
        if source not in self._overrides:
            QMessageBox.information(self, "白名单配置", "基线表条目随版本提供，请在用户层白名单中选择删除。")
            return
        confirm = QMessageBox.question(
            self, "删除用户层白名单条目",
            f"确定删除 {source}→{self._overrides[source]} 吗？",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._overrides.pop(source, None)
        save_overrides(self._overrides)
        self._reset_ocr_cache()
        self.refresh()
