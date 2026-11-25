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
    WATCHED_FILE_NAMES,
    YELLOW,
    FreshnessReport,
    compute_freshness_report,
    load_disabled_items,
    watch_dir,
)
from src.business.rag.corpus_freshness import stale_summary
from src.config.env import DEFAULT_ENV_FILE, is_full_build, save_env_file
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

# 监测项开关定义：key -> 菜单显示名；可见性按构建形态在 _visible_item_keys 裁剪。
# 开关行必须常驻（禁用后判定 item 消失，但开关仍在才能重新勾回），
# 可见性据此独立判定，不随 report.items 派生。
_ITEM_LABELS = (
    ("heroes", "武将"),
    ("guides", "攻略"),
    ("synergies", "相性"),
    ("rankings", "官方榜单"),
    ("corpus", "语料欠账提醒"),
)


class DataFreshnessChip(QLabel):
    """数据新鲜度常驻胶囊；服务信号与 data/ 目录变化驱动刷新，点击弹三组菜单。"""

    fetch_incremental_requested = Signal()
    fetch_specific_requested = Signal()
    rag_maintenance_requested = Signal()  # 语料欠账 → 前往知识库维护（P0-2）

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
        watch_path = watch_dir()
        if not self._watcher.addPath(str(watch_path)):
            logger.warning("数据新鲜度目录监听失败: %s", watch_path)
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
        watch_path = watch_dir()
        for name in WATCHED_FILE_NAMES:
            try:
                snapshot[name] = (watch_path / name).stat().st_mtime
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
        self._add_corpus_section(menu)
        self._add_monitor_section(menu)
        return menu

    def _add_corpus_section(self, menu: QMenu) -> None:
        """语料欠账 section（P0-2）：有待重建任务且主窗口有维护页（完整版）才出现。

        stale_summary 为 mtime-only 毫秒级，菜单打开时现算即可（无需随 _refresh
        缓存）。维护是本地页内操作而非采集任务，无忙碌守卫；精简版无维护页，
        提示无操作出口，不显示。监测项开关取消 corpus 后同样静音（勾选状态
        本身即痕迹，不另出「已隐藏」提示）。
        """
        window = self.window()
        if not hasattr(window, "_rag_maintenance"):
            return
        if "corpus" in load_disabled_items():
            return
        summary = stale_summary()
        if not summary.stale_tasks:
            return
        names = "、".join(summary.stale_tasks[:3])
        if len(summary.stale_tasks) > 3:
            names += "等"
        menu.addSeparator()
        menu.addSection("语料状态")
        action = menu.addAction(
            f"语料 {len(summary.stale_tasks)} 项待重建（{names}→ 前往知识库维护）")
        action.triggered.connect(self.rag_maintenance_requested.emit)

    def _add_monitor_section(self, menu: QMenu) -> None:
        """「监测项」开关 section：取消项完全退出判定与显示，默认全选。

        勾选态每次打开菜单现读 config.env（菜单本就全量重建，UI 零缓存）；
        点击后 QMenu 默认关闭，写回成功经 _refresh 更新胶囊，重开菜单见新态。
        """
        disabled = load_disabled_items()
        labels = dict(_ITEM_LABELS)
        menu.addSeparator()
        menu.addSection("监测项")
        for key in self._visible_item_keys():
            action = menu.addAction(labels[key])
            action.setCheckable(True)
            action.setChecked(key not in disabled)
            action.triggered.connect(lambda _checked=False, k=key: self._toggle_item(k))

    def _visible_item_keys(self) -> tuple[str, ...]:
        """可见开关：武将/榜单恒见；攻略/相性/语料仅完整版，语料还需维护页。

        守卫（至少保留一项）只数其中参与判定的项，见 _toggle_item。
        """
        visible = {"heroes", "rankings"}
        if is_full_build():
            visible |= {"guides", "synergies", "corpus"}
        if "corpus" in visible and not hasattr(self.window(), "_rag_maintenance"):
            visible.discard("corpus")
        return tuple(key for key, _ in _ITEM_LABELS if key in visible)  # 保显示序

    def _toggle_item(self, key: str) -> None:
        """切换监测项：守卫 → 单键集合增删 → 原子写回 → 成功刷新胶囊。

        写失败（OSError，或 config.env 非 UTF-8 时的 UnicodeDecodeError）env 未变，
        改动不保留、只 toast；菜单已随点击关闭，重开时现读 env 自然显示真实勾选态，
        无需回滚。
        """
        disabled = load_disabled_items()
        visible = self._visible_item_keys()
        # 守卫只数参与判定的项：corpus 仅静音提示，不计入「至少一项」也不受其限制——
        # 计入它会让四个可判项经菜单依次全关（判定层随即回全量，菜单显示与实际监测不一致）
        judgeable = set(visible) - {"corpus"}
        if key in judgeable and key not in disabled and len(judgeable - disabled) == 1:
            show_toast(self.window(), "至少保留一项监测", TONE_WARNING)
            return
        new = (disabled - {key}) if key in disabled else (disabled | {key})
        try:
            save_env_file(DEFAULT_ENV_FILE, {
                "DATA_FRESHNESS_DISABLED_ITEMS": ",".join(sorted(new))})
        except UnicodeDecodeError:
            # 记事本「ANSI」保存的 config.env 读不出旧内容，写侧为保留注释拒绝覆盖
            logger.warning(
                "监测项配置写回失败，config.env 非 UTF-8 编码，改动不保留: %s", key,
                exc_info=True)
            show_toast(
                self.window(),
                "保存失败：config.env 需为 UTF-8 编码，请另存为 UTF-8 后重试",
                TONE_WARNING)
            return
        except OSError:
            logger.warning("监测项配置写回失败，改动不保留: %s", key)
            show_toast(self.window(), "保存失败，改动将不保留", TONE_WARNING)
            return
        self._refresh()

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
