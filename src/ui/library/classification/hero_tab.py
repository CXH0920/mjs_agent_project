# -*- coding: utf-8 -*-
"""武将分类维护·武将归类页签（审计 G6 切片 4.5c）。

从 HeroClassificationPanel 拆出：武将筛选/搜索/归类编辑与 LLM 建议归类
（含 _HeroCategoryWorker 后台线程）收敛到本页签，写路径经
ClassificationService；归类变更成功后发 changed 由宿主标记未保存。
名单环境（定位/技能/名单）由宿主经 update_roster() 注入。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from src.business.maintenance.classification_suggest import suggest_hero_categories
from src.business.maintenance.corpus_services import ClassificationService
from src.business.rag.refinement_service import build_generator
from src.ui.shared.checkable_combo import CheckableComboBox
from src.ui.shared.master_detail import MasterDetailPane
from src.ui.shared.style import ROLE_SECONDARY, set_ui_role
from src.ui.shared.widgets import show_toast

logger = logging.getLogger(__name__)

_CLASSIFICATION_FILTERS = ("全部", "未归类", "已归类")


# 持有运行中的 worker，防止页签销毁后 Python 引用丢失导致 QThread 运行中被 GC 析构（#61）
_LIVE_WORKERS: set = set()


class _HeroCategoryWorker(QThread):
    """武将分类 LLM 建议后台线程，避免阻塞 UI。

    parent=None + _LIVE_WORKERS 持有 + finished→deleteLater：生命周期与页签解耦，
    页签销毁不连带析构运行中的线程。run 结束时释放 generator。
    """

    result_ready = Signal(str, object)  # (hero_name, list[str] | None)

    def __init__(self, hero: str, skills_text: str, position: str,
                 categories, generator, parent=None):
        super().__init__(parent)
        self._hero = hero
        self._skills_text = skills_text
        self._position = position
        self._categories = categories
        self._generator = generator
        self._cancelled = False

    def cancel(self) -> None:
        """中断：generator.cancel() 让 _call_api 重试循环退出。"""
        self._cancelled = True
        cancel = getattr(self._generator, "cancel", None)
        if callable(cancel):
            cancel()

    def run(self) -> None:
        _LIVE_WORKERS.add(self)
        try:
            result = suggest_hero_categories(
                self._hero, self._skills_text, self._position,
                self._categories, self._generator)
        finally:
            # worker 即将结束，释放 httpx client（close 安全）
            close = getattr(self._generator, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as error:
                    logger.warning("分类建议 worker 关闭 generator 失败: %s", error)
            _LIVE_WORKERS.discard(self)
        if not self._cancelled:
            self.result_ready.emit(self._hero, result)


class HeroTab(QWidget):
    """「武将归类」页签：武将筛选、机制分类多选与 LLM 建议。"""

    changed = Signal()

    def __init__(self, service: ClassificationService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._repo = service.repository
        self._hero_positions: dict[str, str] = {}
        self._hero_skills: dict[str, str] = {}
        self._hero_names: list[str] = []
        self._current_hero: str | None = None
        self._suggest_worker: _HeroCategoryWorker | None = None
        self._setup_ui()

    # ────────────────────────────────────────────────
    # UI 构建
    # ────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.hero_filter = QComboBox()
        self.hero_filter.addItems(list(_CLASSIFICATION_FILTERS))
        self.hero_filter.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.hero_filter)
        self._hero_search = QLineEdit()
        self._hero_search.setPlaceholderText("搜索武将名称...")
        self._hero_search_timer = QTimer(self)
        self._hero_search_timer.setSingleShot(True)
        self._hero_search_timer.setInterval(150)
        self._hero_search_timer.timeout.connect(self.refresh)
        self._hero_search.textChanged.connect(self._schedule_hero_refresh)
        bar.addWidget(self._hero_search, 1)
        self._hero_count_label = QLabel()
        self._hero_count_label.setObjectName("libraryResultCount")
        bar.addWidget(self._hero_count_label)
        self._goto_unclassified_button = QPushButton("定位未归类")
        set_ui_role(self._goto_unclassified_button, ROLE_SECONDARY)
        self._goto_unclassified_button.clicked.connect(self.goto_next_unclassified)
        bar.addWidget(self._goto_unclassified_button)
        layout.addLayout(bar)

        splitter = MasterDetailPane(
            list_object_name="heroList",
            pane_object_name="heroTabListPane",
            list_min_width=200,
            list_max_width=320,
            sizes=(260, 600),
            with_count_label=False,
            detail_margins=(8, 4, 8, 8),
        )
        self._hero_detail_scroll = splitter.detail_scroll
        self._hero_detail = splitter.detail
        self._hero_detail_layout = splitter.detail_layout
        self._hero_list = splitter.list
        self._hero_list.currentItemChanged.connect(self._on_hero_selected)

        self._hero_empty_label = QLabel("选择左侧武将设置其机制分类。")
        self._hero_empty_label.setObjectName("libraryEmptyState")
        self._hero_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hero_detail_layout.addWidget(self._hero_empty_label)

        self._hero_detail_surface = QFrame()
        self._hero_detail_surface.setObjectName("specialCardDetailSurface")
        surface_layout = QVBoxLayout(self._hero_detail_surface)
        surface_layout.setContentsMargins(20, 18, 20, 20)
        surface_layout.setSpacing(10)
        self._hero_name_label = QLabel()
        self._hero_name_label.setObjectName("cardIdentityName")
        self._hero_name_label.setTextFormat(Qt.TextFormat.PlainText)
        surface_layout.addWidget(self._hero_name_label)
        self._hero_position_label = QLabel()
        self._hero_position_label.setObjectName("metaText")
        self._hero_position_label.setTextFormat(Qt.TextFormat.PlainText)
        self._hero_position_label.setVisible(False)
        surface_layout.addWidget(self._hero_position_label)
        divider = QFrame()
        divider.setObjectName("contentDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        surface_layout.addWidget(divider)
        section = QLabel("机制分类（可多选）")
        section.setObjectName("sectionTitle")
        surface_layout.addWidget(section)
        # 多选组件固定复用，切换武将仅更新值，避免频繁销毁导致的弹层生命周期竞态
        self.hero_combo = CheckableComboBox()
        self.hero_combo.set_items([], default_all=False)
        self.hero_combo.checked_values_changed.connect(self._on_hero_categories_changed)
        surface_layout.addWidget(self.hero_combo)
        hint = QLabel("修改后点击顶部「保存」生效。")
        hint.setObjectName("metaText")
        surface_layout.addWidget(hint)
        self._suggest_category_button = QPushButton("LLM 建议分类")
        set_ui_role(self._suggest_category_button, ROLE_SECONDARY)
        self._suggest_category_button.setEnabled(False)
        self._suggest_category_button.clicked.connect(self._suggest_categories)
        surface_layout.addWidget(self._suggest_category_button)
        surface_layout.addStretch(1)
        self._hero_detail_layout.addWidget(self._hero_detail_surface)

        self._hero_detail_layout.addStretch(1)
        self._hero_detail_scroll.setWidget(self._hero_detail)
        splitter.addWidget(self._hero_detail_scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 600])
        layout.addWidget(splitter, 1)

    @property
    def hero_list(self):
        """武将列表控件（宿主与测试共用）。"""
        return self._hero_list

    @property
    def current_hero(self) -> str | None:
        """当前选中武将名。"""
        return self._current_hero

    # ---------------------------------------------------------------
    # 名单环境与列表刷新
    # ---------------------------------------------------------------
    def update_roster(self, positions: dict[str, str], skills: dict[str, str], names: list[str]) -> None:
        """同步武将名单环境（heroes.json 的名单/定位/技能）并刷新列表。"""
        self._hero_positions, self._hero_skills = positions, skills
        self._hero_names = list(names)
        self.refresh()

    def _filtered_heroes(self) -> list[str]:
        filter_text = self.hero_filter.currentText()
        keyword = self._hero_search.text().strip()
        if filter_text == "未归类":
            heroes = self._repo.list_unclassified()
        elif filter_text == "已归类":
            heroes = self._repo.list_classified()
        else:
            heroes = self._hero_names
        if keyword:
            heroes = [h for h in heroes if keyword in h]
        return heroes

    def _schedule_hero_refresh(self) -> None:
        """搜索防抖：非空输入 150ms 后刷新；清空立即刷新（审计跳转依赖立即生效）。"""
        if self._hero_search.text():
            self._hero_search_timer.start()
        else:
            self._hero_search_timer.stop()
            self.refresh()

    def refresh(self) -> None:
        """重建武将列表并尽量保持选中与滚动位置（#29）。"""
        selected = self._current_hero
        heroes = self._filtered_heroes()
        scroll = self._hero_list.verticalScrollBar().value()
        self._hero_list.setUpdatesEnabled(False)
        try:
            self._hero_list.clear()
            self._hero_count_label.setText(
                f"未归类 {len(self._repo.list_unclassified())} 人 · 显示 {len(heroes)} 人"
            )
            for hero in heroes:
                item = QListWidgetItem(hero)
                item.setData(Qt.ItemDataRole.UserRole, hero)
                self._hero_list.addItem(item)
                if hero == selected:
                    self._hero_list.setCurrentItem(item)
        finally:
            self._hero_list.setUpdatesEnabled(True)
            # 恢复滚动位置（#29），避免刷新后跳回顶部
            self._hero_list.verticalScrollBar().setValue(scroll)
        if not self._hero_list.currentItem():
            self._show_hero_empty()

    def _on_hero_selected(self, current: QListWidgetItem | None, _=None) -> None:
        if current is None:
            self._show_hero_empty()
            return
        self._show_hero_detail(current.data(Qt.ItemDataRole.UserRole))

    def _show_hero_empty(self) -> None:
        self._current_hero = None
        self._hero_detail_surface.setVisible(False)
        self._hero_empty_label.setVisible(True)
        # 归类弹层挂 window()，列表重建后必须显式关闭，否则浮层残留（#30）
        self.hero_combo.closePopup()
        self._suggest_category_button.setEnabled(False)

    def _show_hero_detail(self, hero: str) -> None:
        """更新右侧武将归类详情（复用固定组件，不重建）。"""
        self._current_hero = hero
        self._hero_detail_surface.setVisible(True)
        self._hero_empty_label.setVisible(False)
        self._hero_name_label.setText(hero)
        position = self._hero_positions.get(hero, "")
        self._hero_position_label.setText(f"定位：{position}")
        self._hero_position_label.setVisible(bool(position))
        all_names = [c.name for c in self._repo.list_categories()]
        self.hero_combo.set_items(all_names, default_all=False)
        self.hero_combo.set_checked(self._repo.get_hero_categories(hero))
        # 建议线程运行期间保持禁用，避免并发触发
        self._suggest_category_button.setEnabled(self._suggest_worker is None)

    def _on_hero_categories_changed(self) -> None:
        hero = self._current_hero
        if not hero:
            return
        try:
            self._service.set_hero_categories(hero, sorted(self.hero_combo.checked_values()))
        except ValueError as error:
            QMessageBox.warning(self.window(), "校验失败", str(error))
            # 回滚下拉显示为仓库中的归类，避免视觉与数据不一致（#18）
            self.hero_combo.set_checked(self._repo.get_hero_categories(hero))
            return
        self.changed.emit()

    def on_hero_categories_changed(self) -> None:
        """归类变更入口（测试直调；与勾选回调同一条写路径）。"""
        self._on_hero_categories_changed()

    # ---------------------------------------------------------------
    # LLM 建议归类
    # ---------------------------------------------------------------
    def _ensure_writable(self) -> bool:
        """加载失败（文件损坏）时拒绝所有写操作，防止空数据覆盖原文件（#12）。"""
        if not self._repo.available:
            QMessageBox.warning(self.window(), "数据不可用", "数据文件加载失败，已禁止修改（详情见日志）。")
            return False
        return True

    def _generator(self):
        """取 LLM 生成器；未配置 API Key 时提示并返回 None。"""
        generator = build_generator(None)
        if generator is None:
            QMessageBox.warning(self.window(), "未配置 API",
                "未配置可用的 API 档案（或档案缺少 API Key），无法生成 LLM 建议，可手动归类。")
        return generator

    def _suggest_categories(self) -> None:
        """对当前武将调 LLM 建议归类，后台线程执行不冻结 UI。"""
        hero = self._current_hero
        if not hero or not self._ensure_writable():
            return
        skills_text = self._hero_skills.get(hero, "")
        if not skills_text:
            show_toast(self.window(), f"无 {hero} 的技能文本，无法建议")
            return
        categories = self._repo.list_categories()
        if not categories:
            show_toast(self.window(), "尚无机制分类，请先在「分类管理」新增")
            return
        generator = self._generator()
        if generator is None:
            return
        self._suggest_category_button.setEnabled(False)
        worker = _HeroCategoryWorker(
            hero, skills_text, self._hero_positions.get(hero, ""),
            categories, generator)  # parent=None：页签销毁不连带析构运行中线程
        worker.result_ready.connect(self._on_suggestion_ready)
        worker.finished.connect(self._on_suggest_finished)
        worker.finished.connect(worker.deleteLater)  # 自回收（页签已销毁时也能释放）
        self._suggest_worker = worker
        worker.start()

    def _on_suggestion_ready(self, hero: str, suggested) -> None:
        """LLM 建议返回：回填勾选（set_checked 不发信号，手动触发归类变更）。"""
        if hero != self._current_hero:
            show_toast(self.window(), f"已切换武将，{hero} 的建议未应用，请重新点击")
            return
        if suggested is None:
            show_toast(self.window(), "LLM 建议失败，可手动选择")
            return
        if not suggested:
            show_toast(self.window(), "LLM 未给出建议，可手动选择")
            return
        self.hero_combo.set_checked(suggested)
        # set_checked 不触发 checked_values_changed，手动走归类变更路径写 repo + changed
        self._on_hero_categories_changed()
        show_toast(self.window(), f"已应用 LLM 建议 {len(suggested)} 项，请确认后保存")

    def _on_suggest_finished(self) -> None:
        """worker 结束：清理引用并恢复按钮（worker 由 finished→deleteLater 自回收）。"""
        self._suggest_worker = None
        self._suggest_category_button.setEnabled(self._current_hero is not None)

    # ---------------------------------------------------------------
    # 定位未归类
    # ---------------------------------------------------------------
    def goto_next_unclassified(self) -> None:
        """定位第一个未归类武将；全部已归类时提示完成。"""
        unclassified = self._repo.list_unclassified()
        if not unclassified:
            QMessageBox.information(self.window(), "已完成", "所有武将都已归类。")
            return
        target = unclassified[0]
        self.hero_filter.setCurrentText("未归类")
        self._hero_search.clear()
        for row in range(self._hero_list.count()):
            item = self._hero_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == target:
                self._hero_list.setCurrentItem(item)
                return
