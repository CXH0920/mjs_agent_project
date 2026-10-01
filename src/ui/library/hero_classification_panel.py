# -*- coding: utf-8 -*-
"""武将分类维护面板（知识库维护 → 武将分类维护）。

维护 data/hero_classification.json：分类管理 / 克制链 / 武将归类。
三个页签的交互收敛在 src/ui/library/classification/ 下（审计 G6 切片 4.5），
本面板只负责页签装配、加载错误防护、dirty 协议与保存编排；保存后发
data_changed 供知识库维护页刷新语料状态。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget
from src.business.maintenance.corpus_services import ClassificationService
from src.business.rag.hero_brief import load_hero_briefs
from src.ui.library.classification.category_tab import CategoryTab
from src.ui.library.classification.chain_tab import ChainTab
from src.ui.library.classification.hero_tab import HeroTab
from src.ui.shared.style import ROLE_PRIMARY, ROLE_SECONDARY, TONE_INFO, TONE_SUCCESS, TONE_WARNING
from src.ui.shared.widgets import PageActionBar, show_toast

if TYPE_CHECKING:
    from src.data.hero_classification_repository import HeroClassificationRepository

logger = logging.getLogger(__name__)


class HeroClassificationPanel(QWidget):
    """知识库维护 → 武将分类维护：分类 / 克制链 / 武将归类。"""

    data_changed = Signal()

    def __init__(self, repository: HeroClassificationRepository,
                 hero_positions: dict[str, str] | None = None,
                 hero_skills: dict[str, str] | None = None, *,
                 root: Path, parent=None):
        super().__init__(parent)
        # 写路径经业务服务（#A1）；读查询沿用 _repo 透传（测试直接使用）
        self._service = ClassificationService(repository)
        self._repo = self._service.repository
        self._root = root
        self._dirty = False
        self._load_errors = False
        self._setup_ui()
        # 名单环境初值经页签注入，与 refresh_roster 同一条路径
        self._hero_tab.update_roster(hero_positions or {}, hero_skills or {},
                                     sorted(repository.hero_names))
        self.reload_data()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------
    def _setup_ui(self) -> None:
        self.setObjectName("heroClassificationPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._action_bar = PageActionBar("正在加载……", self)
        self._status_label = self._action_bar.status_label
        self._refresh_button = QPushButton("刷新")
        # clicked 信号自带 False 参数，直接 connect 会旁路 confirm_discard，必须经 lambda
        self._refresh_button.clicked.connect(lambda _=False: self.reload_data(confirm_discard=True))
        self._action_bar.add_action(self._refresh_button, ROLE_SECONDARY)
        self._save_button = QPushButton("保存")
        self._save_button.clicked.connect(self._save)
        self._action_bar.add_action(self._save_button, ROLE_PRIMARY)
        layout.addWidget(self._action_bar)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("sectionTabs")
        # 分类增删改后由页签发 changed：刷新克制链下拉并标记未保存
        # （原 _refresh_categories 尾部对克制链的联动，见 _on_category_tab_changed）
        self._category_tab = CategoryTab(self._service, self)
        self._category_tab.changed.connect(self._on_category_tab_changed)
        self._tabs.addTab(self._category_tab, "分类管理")
        self._chain_tab = ChainTab(self._service, self)
        self._chain_tab.changed.connect(self._mark_dirty)
        self._tabs.addTab(self._chain_tab, "克制链")
        self._hero_tab = HeroTab(self._service, self)
        self._hero_tab.changed.connect(self._mark_dirty)
        self._tabs.addTab(self._hero_tab, "武将归类")
        layout.addWidget(self._tabs, 1)

    def _on_category_tab_changed(self) -> None:
        """分类增删改后联动：克制链下拉随分类集刷新，并标记未保存。"""
        self._chain_tab.refresh_options()
        self._mark_dirty()

    # ---------------------------------------------------------------
    # 数据加载与保存
    # ---------------------------------------------------------------
    def reload_data(self, confirm_discard: bool = True) -> None:
        """重新加载数据。

        - 有未保存修改时先确认（刷新/重载入口），确认后丢弃并重置 dirty；
        - 同步刷新武将名单环境（heroes.json，见 refresh_roster）；
        - 加载失败（error）时在状态栏提示并禁用「保存」，防止空数据覆盖原文件。
        """
        if self._dirty:
            if confirm_discard:
                answer = QMessageBox.question(
                    self, "丢弃未保存修改",
                    "有未保存的修改，重新加载将丢弃这些修改。继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            self._dirty = False
        issues = self._repo.load()
        errors = [item.message for item in issues if item.severity == "error"]
        self._load_errors = bool(errors)
        self.refresh_roster()
        self._category_tab.refresh()
        self._chain_tab.refresh_options()
        if errors:
            self._action_bar.set_status(f"加载异常 {len(errors)} 条（详见日志），已禁止保存", TONE_WARNING)
            self._save_button.setEnabled(False)
        else:
            self._save_button.setEnabled(True)
            self._update_status("已加载", TONE_INFO)

    def refresh_roster(self) -> None:
        """同步武将名单环境（heroes.json 的名单/定位/技能），不触碰归类编辑数据。

        爬虫/公告更新 heroes.json 后由各刷新入口调用；读取失败时保留现有
        名单（fallback 语义），面板不因数据文件缺失而瘫痪。
        """
        try:
            names, positions, skills = load_hero_briefs(self._root, self._repo.hero_names)
        except Exception:
            logger.exception("刷新武将名单失败，保留现有名单")
            return
        self._repo.update_hero_names(names)
        self._hero_tab.update_roster(positions, skills, sorted(names))

    def _update_status(self, text: str, tone: str) -> None:
        # 加载失败时保持只读提示，不被编辑状态文案覆盖（#38）
        if self._load_errors:
            self._action_bar.set_status("加载异常，已禁止修改（详见日志）", TONE_WARNING)
            return
        if self._dirty:
            self._action_bar.set_status(f"{text} · 有未保存修改", TONE_WARNING)
        else:
            self._action_bar.set_status(text, tone)

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._update_status("已修改", TONE_WARNING)

    def _save(self) -> None:
        if not self._repo.available:
            QMessageBox.warning(self, "数据不可用", "数据文件加载失败，已禁止保存（详情见日志）。")
            return
        try:
            self._service.save()
        except Exception as error:
            QMessageBox.critical(self, "保存失败", str(error))
            # 仓库已回滚内存；丢弃未保存标记并对齐磁盘状态
            self._dirty = False
            self.reload_data(confirm_discard=False)
            return
        self._dirty = False
        self._hero_tab.refresh()
        self._update_status("已保存", TONE_SUCCESS)
        self.data_changed.emit()
        show_toast(self, "武将分类数据已保存，请在知识库维护中重建语料")

    def focus_unclassified(self) -> None:
        """切到「武将归类」子页签并定位第一个未归类武将（供知识库维护审计跳转）。"""
        self._tabs.setCurrentIndex(self._tabs.indexOf(self._hero_tab))
        # 审计基于磁盘现读，跳转前先同步名单环境，保证 heroes.json 的新武将可被定位
        self.refresh_roster()
        if not self._repo.list_unclassified():
            return
        self._hero_tab.goto_next_unclassified()
