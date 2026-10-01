# -*- coding: utf-8 -*-
"""选将推荐·实战配队横条（审计 G5 切片 4.4a）。

从 RecommendationPanel 拆出：当前识别 8 人中命中的 combos 配队横条
（chip 列表、展开/收起、管理入口、详情弹窗）收敛到本控件。横条只消费
combo_manager 只读列表，不持有识别状态；宿主经 refresh(hero_ids) 驱动，
评级结果经 ratings_computed 信号回传供宿主刷新卡片角标（卡片耦合留在宿主）。
combo_manager 允许为 None（测试/无配队数据场景）：横条隐藏、refresh 返回空集。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from src.business.maintenance.corpus_services import ComboService
from src.ui.library.combo_management_dialog import ComboManagementDialog
from src.ui.shared.combo_detail import show_combo_detail
from src.ui.shared.combo_format import format_seats
from src.ui.shared.widgets import FlowLayout

logger = logging.getLogger(__name__)


class ComboStrip(QWidget):
    """实战配队横条：命中配队 chip 流式展示与管理入口。"""

    ratings_computed = Signal(dict)  # hero_id -> 参战配队最高评级

    def __init__(self, hero_mgr, combo_mgr, parent=None) -> None:
        super().__init__(parent)
        self._hero_mgr = hero_mgr
        self._combo_mgr = combo_mgr
        self._matched_combos: list = []
        self._combo_chips_collapsed = False
        self._last_hero_ids: set[int] = set()
        self._setup_ui()
        self.setVisible(False)

    def _setup_ui(self) -> None:
        self.setObjectName("recommendationComboStrip")
        strip_layout = QVBoxLayout(self)
        strip_layout.setContentsMargins(8, 6, 8, 6)
        strip_layout.setSpacing(4)

        strip_header = QHBoxLayout()
        self._combo_title = QLabel("⚔ 实战配队")
        self._combo_title.setObjectName("recommendationComboTitle")
        strip_header.addWidget(self._combo_title)
        strip_header.addStretch()
        self._combo_manage_btn = QPushButton("管理")
        self._combo_manage_btn.setToolTip("新增、编辑或删除实战配队")
        self._combo_manage_btn.setAccessibleName("管理实战配队")
        self._combo_manage_btn.clicked.connect(self._open_combo_management)
        strip_header.addWidget(self._combo_manage_btn)
        self._combo_toggle_btn = QToolButton()
        self._combo_toggle_btn.setObjectName("recommendationComboToggle")
        self._combo_toggle_btn.setText("收起")
        self._combo_toggle_btn.setAccessibleName("收起实战配队列表")
        self._combo_toggle_btn.clicked.connect(self._toggle_combo_chips)
        strip_header.addWidget(self._combo_toggle_btn)
        strip_layout.addLayout(strip_header)

        self._combo_chips_container = QWidget()
        self._combo_chip_flow = FlowLayout(self._combo_chips_container, spacing=6)
        strip_layout.addWidget(self._combo_chips_container)

    # ---------------------------------------------------------------
    # 刷新（宿主入口）
    # ---------------------------------------------------------------
    def refresh(self, hero_ids: set[int]) -> dict[int, int]:
        """按当前识别的武将集合匹配实战配队，重建横条；回传各武将最高评级。"""
        self._last_hero_ids = set(hero_ids)
        self._matched_combos = []
        if self._combo_mgr is not None and len(hero_ids) >= 2:
            for combo in self._combo_mgr.list_combos():
                if combo.hero1_id in hero_ids and combo.hero2_id in hero_ids:
                    self._matched_combos.append(combo)
            self._matched_combos.sort(key=lambda c: (-c.rating, c.hero1_name, c.hero2_name))
        best_rating = self._compute_best_ratings()
        self._render_combo_chips()
        self.ratings_computed.emit(best_rating)
        return best_rating

    def _compute_best_ratings(self) -> dict[int, int]:
        """参战配队的最高评级表（宿主据此刷卡片"实战 ★评级"角标）。"""
        best_rating: dict[int, int] = {}
        for combo in self._matched_combos:
            for hero_id in (combo.hero1_id, combo.hero2_id):
                best_rating[hero_id] = max(best_rating.get(hero_id, 0), combo.rating)
        return best_rating

    def _render_combo_chips(self) -> None:
        """重建配队 chip：按评级降序展示全部命中配队。"""
        while self._combo_chip_flow.count():
            item = self._combo_chip_flow.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()

        for combo in self._matched_combos:
            chip = QPushButton(
                f"★{combo.rating} {combo.hero1_name}[{format_seats(combo.hero1_seats)}]"
                f" + {combo.hero2_name}[{format_seats(combo.hero2_seats)}]"
            )
            chip.setObjectName("recommendationComboChip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(self._combo_tooltip(combo))
            chip.clicked.connect(lambda checked=False, target=combo: self._show_combo_detail(target))
            self._combo_chip_flow.addWidget(chip)
        self.setVisible(bool(self._matched_combos))

    @staticmethod
    def _combo_tooltip(combo) -> str:
        seats = (
            f"{combo.hero1_name}[{format_seats(combo.hero1_seats)}] "
            f"+ {combo.hero2_name}[{format_seats(combo.hero2_seats)}]"
        )
        return f"{seats}\n{combo.note}" if combo.note else seats

    def _toggle_combo_chips(self) -> None:
        self._combo_chips_collapsed = not self._combo_chips_collapsed
        self._combo_chips_container.setVisible(not self._combo_chips_collapsed)
        self._combo_toggle_btn.setText("展开" if self._combo_chips_collapsed else "收起")

    def _open_combo_management(self) -> None:
        """打开实战配队全量管理对话框；增删改后即时刷新命中条。"""
        dialog = ComboManagementDialog(
            self._hero_mgr, ComboService(self._combo_mgr), self)
        dialog.combos_changed.connect(lambda: self.refresh(self._last_hero_ids))
        dialog.exec()

    def _show_combo_detail(self, combo) -> None:
        """配队详情：2×2 号位示意 + 座次要求 + note 原文。"""
        show_combo_detail(self, combo)
