# -*- coding: utf-8 -*-
"""状态栏数据新鲜度芯片：武将/攻略/相性/官方榜单四类数据健康度常驻胶囊。

点击（或 Space/Enter/Down）弹出纯 QMenu 三组：内容年龄清单 / 数据源周期 /
建议操作。刷新自足：订阅三类采集完成、生成重载、公告检查完成信号，并以
QFileSystemWatcher 监听 data/ 目录（目标文件 mtime 快照过滤 + 去抖，覆盖
终端 pull_data 同步的原子落盘）。判定逻辑在 src/data/freshness.py（纯函数）。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QFileSystemWatcher, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QLabel, QMenu
from src.business.freshness import (
    RED,
    SOURCE_CYCLE_ROWS,
    SUGGEST_INCREMENTAL,
    SUGGEST_SPECIFIC,
    WATCH_DIR,
    WATCHED_FILE_NAMES,
    YELLOW,
    FreshnessReport,
    compute_freshness_report,
)
from src.ui.shared.style import TONE_WARNING
from src.ui.shared.widgets import show_toast

logger = logging.getLogger(__name__)

_WATCH_DEBOUNCE_MS = 500

# 与 status_chips 现有色板同值的档位配色（前景/背景）
_CHIP_STYLES = {
    "green": ("#176b36", "#e4f5e8"),
    "yellow": ("#8a5a00", "#fff3cd"),
    "red": ("#a12622", "#fde8e8"),
}

# 黄/红档次的非武将项建议文案（武将项由 suggested_action 路由成可点动作）
_ADVICE_ROWS = {
    "guides": "攻略落后或未生成：建议在资料库重新生成攻略",
    "synergies": "相性落后或未生成：建议在资料库重新生成相性",
    "rankings": "榜单过期或缺失：建议通过 OCR 导入最新榜单",
}


class DataFreshnessChip(QLabel):
    """数据新鲜度常驻胶囊；服务信号与 data/ 目录变化驱动刷新，点击弹三组菜单。"""

    fetch_incremental_requested = Signal()
    fetch_specific_requested = Signal()

    def __init__(
        self,
        hero_fetch_service,
        guide_fetch_service,
        synergy_fetch_service,
        ai_workflow,
        announcement_service,
        parent=None,
    ):
        super().__init__(parent)
        self._hero_fetch_service = hero_fetch_service
        self._report: FreshnessReport | None = None
        self._watched_mtimes: dict[str, float] = {}
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("数据新鲜度")
        self.setToolTip("点击查看数据新鲜度详情与建议操作")

        # 刷新接线：三类采集完成（hero 为 Signal(bool)，guide/synergy 为
        # Signal(bool, str)，lambda 归一）/ 生成重载 / 公告检查完成
        hero_fetch_service.fetch_completed.connect(lambda _ok: self._refresh())
        guide_fetch_service.fetch_completed.connect(lambda *_: self._refresh())
        synergy_fetch_service.fetch_completed.connect(lambda *_: self._refresh())
        ai_workflow.guides_changed.connect(self._refresh)
        ai_workflow.synergies_changed.connect(self._refresh)
        announcement_service.check_finished.connect(lambda _result: self._refresh())

        self._watcher = QFileSystemWatcher(self)
        if not self._watcher.addPath(str(WATCH_DIR)):
            logger.warning("数据新鲜度目录监听失败: %s", WATCH_DIR)
        self._watcher.directoryChanged.connect(lambda _path: self._debounce.start())
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_WATCH_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._on_debounce_timeout)
        self._refresh()

    # ── 刷新与渲染 ────────────────────────────────────────────

    def _refresh(self) -> None:
        self._report = compute_freshness_report()
        self._watched_mtimes = self._snapshot_mtimes()
        color, background = _CHIP_STYLES[self._report.state]
        self.setText(self._report.headline)
        self.setStyleSheet(
            f"color: {color}; background-color: {background}; padding: 3px 8px; "
            "border-radius: 8px; font-weight: bold;"
        )

    @staticmethod
    def _snapshot_mtimes() -> dict[str, float]:
        snapshot: dict[str, float] = {}
        for name in WATCHED_FILE_NAMES:
            try:
                snapshot[name] = (WATCH_DIR / name).stat().st_mtime
            except OSError:
                continue
        return snapshot

    def _on_debounce_timeout(self) -> None:
        if self._snapshot_mtimes() != self._watched_mtimes:
            self._refresh()

    # ── 交互与菜单 ────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._open_menu()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (
            Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Down,
        ):
            self._open_menu()
            event.accept()
            return
        super().keyPressEvent(event)

    def _open_menu(self) -> None:
        """在芯片下方弹出菜单（QMenu 自带屏幕边缘避让）。"""
        menu = self._build_menu(self._report)
        menu.exec(self.mapToGlobal(QPoint(0, self.height())))

    def _build_menu(self, report: FreshnessReport | None) -> QMenu:
        menu = QMenu(self)
        menu.addSection("内容年龄")
        if report is None:
            self._add_disabled_row(menu, "数据：读取中…")
        else:
            for item in report.items:
                self._add_disabled_row(menu, item.detail)
            if report.timeline_warning:
                self._add_disabled_row(menu, f"⚠ {report.timeline_warning}")
        menu.addSeparator()
        menu.addSection("数据源周期")
        for row in SOURCE_CYCLE_ROWS:
            self._add_disabled_row(menu, row)
        suggestions = self._suggestion_rows(report)
        if suggestions:
            menu.addSeparator()
            menu.addSection("建议操作")
            for text, kind in suggestions:
                if kind == SUGGEST_INCREMENTAL:
                    menu.addAction(text).triggered.connect(self._request_incremental)
                elif kind == SUGGEST_SPECIFIC:
                    menu.addAction(text).triggered.connect(self._request_specific)
                else:
                    self._add_disabled_row(menu, text)
        return menu

    @staticmethod
    def _add_disabled_row(menu: QMenu, text: str) -> QAction:
        action = menu.addAction(text)
        action.setEnabled(False)
        return action

    @staticmethod
    def _suggestion_rows(report: FreshnessReport | None) -> list[tuple[str, str | None]]:
        """武将落后事件按模式能力路由成可点动作（混合批次两者并给）；
        其余黄/红项补 disabled 建议文案行。"""
        rows: list[tuple[str, str | None]] = []
        if report is None:
            return rows
        for action in report.suggested_actions:
            if action == SUGGEST_INCREMENTAL:
                rows.append(("增量获取武将数据（拉取新增武将）", SUGGEST_INCREMENTAL))
            elif action == SUGGEST_SPECIFIC:
                rows.append(("指定获取武将数据（替换模式，刷新过期武将）…", SUGGEST_SPECIFIC))
        for item in report.items:
            if item.state in (YELLOW, RED) and item.key in _ADVICE_ROWS:
                rows.append((_ADVICE_ROWS[item.key], None))
        return rows

    # ── 建议动作（忙碌守卫 → 信号回主窗口采集入口） ───────────

    def _request_incremental(self) -> None:
        if self._hero_fetch_service.is_busy:
            show_toast(self.window(), "任务进行中", TONE_WARNING)
            return
        self.fetch_incremental_requested.emit()

    def _request_specific(self) -> None:
        if self._hero_fetch_service.is_busy:
            show_toast(self.window(), "任务进行中", TONE_WARNING)
            return
        self.fetch_specific_requested.emit()
