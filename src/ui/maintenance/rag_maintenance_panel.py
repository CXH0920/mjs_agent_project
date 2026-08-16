# -*- coding: utf-8 -*-
"""知识库维护工作台（主导航第 4 页）。

功能：
- 变更预览：对比权威源（data/、docs/）与 data/rag_corpus 语料的修改时间，标记 8 个语料任务状态；
- 审计提示：未归类武将、专属牌引用未知武将等人工维护清单；
- 一键执行：本地运行 scripts/maintain_rag.py 重建语料/索引（不再依赖外部 mjs 仓库），实时输出日志。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.data.hero_classification_repository import HeroClassificationRepository
from src.data.special_cards_repository import SpecialCardRepository
from src.data.equip_attrs_repository import EquipAttrsRepository
from src.data.card_points_repository import CardPointsRepository
from src.business.rag.refinement_service import list_pending
from src.ui.library.hero_classification_panel import HeroClassificationPanel
from src.ui.library.special_cards_panel import SpecialCardsPanel
from src.ui.maintenance.equip_attrs_panel import EquipAttrsPanel
from src.ui.maintenance.card_points_panel import CardPointsPanel

from src.config.env import PROJECT_ROOT
from src.ui.shared.style import (
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    SPACE_MD,
    SPACE_SM,
    TONE_SUCCESS,
    TONE_WARNING,
    set_tone,
    set_ui_role,
)
from src.ui.shared.widgets import PageActionBar
from src.ui.maintenance.index_refinement_dialog import IndexRefinementDialog
from src.ui.maintenance.rule_doc_panel import RuleDocPanel

# 语料任务：名称 -> (源文件, 输出语料文件)
TASK_DEFS: list[tuple[str, list[str], list[str]]] = [
    ("武将语料", ["data/heroes.json", "data/cards.json"], ["武将RAG语料.json"]),
    ("卡牌语料", ["data/cards.json"], ["卡牌RAG语料.json"]),
    ("点数花色语料", ["data/card_points.json"], ["卡牌点数花色语料.json"]),
    ("装备属性语料", ["data/cards.json", "data/equip_attrs.json"], ["装备属性语料.json"]),
    ("加强削弱语料", ["data/cards.json", "data/card_annotations.json"], ["加强削弱语料.json"]),
    ("元规则/术语/FAQ", ["docs/元规则整理-完整版.md"],
     ["元规则RAG语料-章节块.json", "术语表.json", "FAQ裁定块.json"]),
    ("特殊机制语料", ["data/special_cards.json"], ["特殊机制语料.json"]),
    ("武将分类语料", ["data/hero_classification.json", "data/heroes.json"], ["武将分类语料.json"]),
]

CORPUS_DIR = "data/rag_corpus"
PYTHON = sys.executable


@dataclass(frozen=True)
class AuditIssue:
    """人工维护提示条目（结构化，供 UI 渲染跳转按钮）。

    - kind: 问题类型标识（unclassified_hero / unknown_hero / missing_settlement / bad_card_points 等）；
    - target_tab: 跳转目标一级页签名（空表示无跳转）；
    - target: 定位数据，按 kind 解释（未归类武将名列表 / 专属牌 (category, name) / 牌名列表）。
    """

    kind: str
    message: str
    severity: str = "warning"
    target_tab: str = ""
    target: object = None


def format_audit_issues(issues: list[AuditIssue]) -> list[str]:
    """结构化审计条目 → 纯文本列表（兼容旧消费方/测试）。"""
    return [issue.message for issue in issues]


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


def audit_summary(root: Path) -> list[AuditIssue]:
    """返回人工维护提示清单（结构化条目；空列表表示无问题）。"""
    issues: list[AuditIssue] = []
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
            issues.append(AuditIssue(
                kind="unclassified_hero",
                message=f"未归类武将 {len(unclassified)} 人（请补充 data/hero_classification.json）",
                target_tab="武将分类维护",
                target=unclassified,
            ))
    except (OSError, json.JSONDecodeError):
        issues.append(AuditIssue(
            kind="classification_unreadable",
            message="data/hero_classification.json 缺失或无法解析",
            target_tab="武将分类维护",
        ))
    try:
        specials = json.loads(special_path.read_text(encoding="utf-8"))
        unknown = set()
        for item in specials:
            hero = item.get("hero", "")
            if not hero:
                continue
            for _name in re.split(r"[\u3001,]", hero):
                _name = re.split(r"[(\uff08]", _name, 1)[0].strip()
                if not _name or _name in ("通用", "—", "众多武将") or _name.endswith("等"):
                    continue
                if _name not in hero_names:
                    unknown.add(_name)
        unknown = sorted(unknown)
        if unknown:
            target = next(
                ((str(it.get("category", "")), str(it.get("name", ""))) for it in specials
                 if it.get("hero") and any(n in it["hero"] for n in unknown)),
                None,
            )
            issues.append(AuditIssue(
                kind="unknown_hero",
                message=f"专属牌引用未知武将 {len(unknown)} 人：{'、'.join(unknown[:8])}",
                target_tab="专属牌维护",
                target=target,
            ))
        # 专属牌/战法牌结算详情回填校验（死士为非实体牌标记，xlsx 无对应结算，豁免）
        missing_items = [
            it for it in specials
            if it.get("category") in ("专属牌", "专属战法牌")
            and not it.get("settlement") and it.get("name") not in ("死士",)
        ]
        if missing_items:
            names = [str(it.get("name", "")) for it in missing_items]
            first = missing_items[0]
            issues.append(AuditIssue(
                kind="missing_settlement",
                message=f"专属牌/战法牌缺结算详情 {len(missing_items)} 个：{'、'.join(names[:8])}",
                target_tab="专属牌维护",
                target=(str(first.get("category", "")), str(first.get("name", ""))),
            ))
    except (OSError, json.JSONDecodeError):
        issues.append(AuditIssue(
            kind="specials_unreadable",
            message="data/special_cards.json 缺失或无法解析",
            target_tab="专属牌维护",
        ))
    # 卡牌点数源校验（data/card_points.json，原 xlsx sheet1 + 判定规则）
    points_path = root / "data" / "card_points.json"
    try:
        payload = json.loads(points_path.read_text(encoding="utf-8"))
        cards = payload.get("cards") if isinstance(payload, dict) else None
        if not isinstance(cards, list):
            issues.append(AuditIssue(
                kind="card_points_structure",
                message="data/card_points.json 结构异常（缺少 cards 数组）",
                target_tab="卡牌点数维护",
            ))
        else:
            valid_suits = ("♥", "♣", "♠", "♦", "太极")
            valid_points = {str(i) for i in range(1, 9)}
            bad_suits = sorted({c.get("name", "?") for c in cards if c.get("suit") not in valid_suits})
            bad_points = sorted({c.get("name", "?") for c in cards if c.get("point") not in valid_points})
            total = sum(int(c.get("count", 1) or 1) for c in cards)
            if total != 162:
                issues.append(AuditIssue(
                    kind="card_points_total",
                    message=f"卡牌点数张数 {total} != 期望 162",
                    target_tab="卡牌点数维护",
                ))
            if bad_suits:
                issues.append(AuditIssue(
                    kind="bad_card_points",
                    message=f"卡牌点数异常花色 {len(bad_suits)} 张：{'、'.join(bad_suits[:6])}",
                    target_tab="卡牌点数维护",
                ))
            if bad_points:
                issues.append(AuditIssue(
                    kind="bad_card_points",
                    message=f"卡牌点数异常点数 {len(bad_points)} 张：{'、'.join(bad_points[:6])}",
                    target_tab="卡牌点数维护",
                ))
    except (OSError, json.JSONDecodeError):
        issues.append(AuditIssue(
            kind="card_points_unreadable",
            message="data/card_points.json 缺失或无法解析",
            target_tab="卡牌点数维护",
        ))
    # 装备属性源校验（data/equip_attrs.json，原 xlsx sheet2）
    equips_path = root / "data" / "equip_attrs.json"
    try:
        equips = json.loads(equips_path.read_text(encoding="utf-8"))
        if not isinstance(equips, list):
            issues.append(AuditIssue(
                kind="equip_attrs_structure",
                message="data/equip_attrs.json 结构异常（应为数组）",
                target_tab="装备属性维护",
            ))
        else:
            if len(equips) != 26:
                issues.append(AuditIssue(
                    kind="equip_attrs_count",
                    message=f"装备属性件数 {len(equips)} != 期望 26",
                    target_tab="装备属性维护",
                ))
            for item in equips:
                if item.get("subtype") not in ("武器", "防具", "坐骑"):
                    issues.append(AuditIssue(
                        kind="bad_equip_attrs",
                        message=f"装备 {item.get('name', '?')} 细分类型异常：{item.get('subtype')!r}",
                        target_tab="装备属性维护",
                    ))
                if item.get("distance_mod") not in (None, -1, 1):
                    issues.append(AuditIssue(
                        kind="bad_equip_attrs",
                        message=f"装备 {item.get('name', '?')} 距离修正异常：{item.get('distance_mod')!r}",
                        target_tab="装备属性维护",
                    ))
    except (OSError, json.JSONDecodeError):
        issues.append(AuditIssue(
            kind="equip_attrs_unreadable",
            message="data/equip_attrs.json 缺失或无法解析",
            target_tab="装备属性维护",
        ))
    # 索引精化待办：无 curated 且索引字段为空的语料块（语料未构建时清单为空，静默跳过）
    pending_refinement = list_pending(root / CORPUS_DIR)
    if pending_refinement:
        issues.insert(0, AuditIssue(
            kind="pending_refinement",
            message=f"索引字段待精化 {len(pending_refinement)} 块（卡牌/武将语料）",
            severity="warning",
        ))
    return issues


class RagMaintenancePanel(QWidget):
    """知识库维护工作台。"""

    data_changed = Signal()

    def __init__(self, root: Path = PROJECT_ROOT, hero_names: set[str] | None = None, parent=None):
        super().__init__(parent)
        self._root = root
        self._proc: QProcess | None = None
        self._hero_names, self._hero_positions = self._load_heroes(self._root, hero_names)
        self._setup_ui()
        self.refresh()

    @staticmethod
    def _load_heroes(root: Path, fallback: set[str] | None) -> tuple[set[str], dict[str, str]]:
        """从 data/heroes.json 读取武将名与定位；文件缺失时使用传入集合。"""
        heroes_path = root / "data" / "heroes.json"
        try:
            heroes = json.loads(heroes_path.read_text(encoding="utf-8"))
            names = {str(h.get("name", "")) for h in heroes if h.get("name")}
            positions = {str(h.get("name", "")): str(h.get("position", "") or "")
                         for h in heroes if h.get("name")}
            return names, positions
        except (OSError, json.JSONDecodeError, ValueError):
            return set(fallback or ()), {}

    def _setup_ui(self) -> None:
        self.setObjectName("ragMaintenancePanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("librarySectionTabs")
        status_tab = QWidget()
        status_layout = QVBoxLayout(status_tab)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(8)

        self._action_bar = PageActionBar("正在检查……", self)
        self._status_label = self._action_bar.status_label
        # 查看类操作
        self._refresh_button = QPushButton("刷新状态")
        self._refresh_button.clicked.connect(self.refresh)
        self._action_bar.add_action(self._refresh_button, ROLE_SECONDARY)
        self._refine_button = QPushButton("索引精化")
        self._refine_button.clicked.connect(self._open_refinement)
        self._action_bar.add_action(self._refine_button, ROLE_SECONDARY)
        # 分隔线：查看类 | 执行类
        _divider = QFrame()
        _divider.setObjectName("actionBarDivider")
        _divider.setFrameShape(QFrame.Shape.VLine)
        _divider.setFixedHeight(18)
        self._action_bar.actions_layout.addWidget(_divider)
        # 执行类操作
        self._hero_button = QPushButton("重建武将语料")
        self._hero_button.clicked.connect(lambda: self._run(["--force", "--only", "武将"]))
        self._action_bar.add_action(self._hero_button, ROLE_SECONDARY)
        self._corpus_button = QPushButton("重建全部语料")
        self._corpus_button.clicked.connect(lambda: self._run(["--force"]))
        self._action_bar.add_action(self._corpus_button, ROLE_SECONDARY)
        self._index_button = QPushButton("重建语料+索引")
        self._index_button.clicked.connect(lambda: self._run(["--force", "--build-index"]))
        self._action_bar.add_action(self._index_button, ROLE_PRIMARY)
        status_layout.addWidget(self._action_bar)

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

        # 审计区：标题 + 逐条提示行（每条可带跳转按钮）
        self._audit_banner = QFrame()
        self._audit_banner.setObjectName("noticeBanner")
        banner_layout = QHBoxLayout(self._audit_banner)
        banner_layout.setContentsMargins(SPACE_MD, SPACE_SM, SPACE_MD, SPACE_SM)
        banner_layout.setSpacing(SPACE_MD)
        banner_text_layout = QVBoxLayout()
        banner_text_layout.setContentsMargins(0, 0, 0, 0)
        banner_text_layout.setSpacing(2)
        self._audit_label = QLabel("人工维护检查通过")
        self._audit_label.setObjectName("noticeBannerTitle")
        banner_text_layout.addWidget(self._audit_label)
        self._audit_list = QWidget()
        self._audit_list_layout = QVBoxLayout(self._audit_list)
        self._audit_list_layout.setContentsMargins(0, 0, 0, 0)
        self._audit_list_layout.setSpacing(4)
        self._audit_rows: list[QWidget] = []
        banner_text_layout.addWidget(self._audit_list)
        banner_layout.addLayout(banner_text_layout, 1)
        set_tone(self._audit_banner, TONE_SUCCESS)

        # 上半：任务表 + 审计；下半：日志（QSplitter 可拖拽/折叠）
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)
        top_layout.addWidget(self._table_surface, 1)
        top_layout.addWidget(self._audit_banner)

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
        self._log.setMinimumHeight(60)
        log_layout.addWidget(self._log)

        _splitter = QSplitter(Qt.Orientation.Vertical)
        _splitter.setChildrenCollapsible(True)
        _splitter.addWidget(top_widget)
        _splitter.addWidget(self._log_surface)
        _splitter.setSizes([520, 180])
        status_layout.addWidget(_splitter, 1)
        self._tabs.addTab(status_tab, "语料状态")

        # 文档规则域（T0 母本入口，排在数据编辑页签之前）
        self._rule_doc = RuleDocPanel(self._root)
        self._rule_doc.data_changed.connect(self._on_child_changed)
        self._tabs.addTab(self._rule_doc, "元规则维护")

        self._special_cards = SpecialCardsPanel(
            SpecialCardRepository(self._root / "data" / "special_cards.json"), self._hero_names)
        self._special_cards.data_changed.connect(self._on_child_changed)
        self._tabs.addTab(self._special_cards, "专属牌维护")

        self._card_points = CardPointsPanel(
            CardPointsRepository(self._root / "data" / "card_points.json"), self._root)
        self._card_points.data_changed.connect(self._on_child_changed)
        self._tabs.addTab(self._card_points, "卡牌点数维护")

        self._equip_attrs = EquipAttrsPanel(
            EquipAttrsRepository(self._root / "data" / "equip_attrs.json"))
        self._equip_attrs.data_changed.connect(self._on_child_changed)
        self._tabs.addTab(self._equip_attrs, "装备属性维护")

        self._classification = HeroClassificationPanel(
            HeroClassificationRepository(
                self._root / "data" / "hero_classification.json", self._hero_names),
            self._hero_positions)
        self._classification.data_changed.connect(self._on_child_changed)
        self._tabs.addTab(self._classification, "武将分类维护")

        layout.addWidget(self._tabs, 1)

    def _on_child_changed(self) -> None:
        """专属牌/武将分类保存后：刷新语料状态并转发 data_changed。"""
        self.refresh()
        self.data_changed.emit()

    def reload_data(self) -> None:
        """重新加载语料状态与四个子维护面板。"""
        self.refresh()
        self._special_cards.reload_data()
        self._classification.reload_data()
        self._card_points.reload_data()
        self._equip_attrs.reload_data()
        self._rule_doc.reload_data()

    def refresh(self) -> None:
        rows = task_states(self._root)
        # 索引精化入口：按钮带待精化数量角标；无待办时禁用
        pending_count = len(list_pending(self._root / "data" / "rag_corpus"))
        self._refine_button.setText(f"索引精化（{pending_count}）" if pending_count else "索引精化 ✓")
        self._refine_button.setEnabled(pending_count > 0)
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
            set_tone(self._audit_banner, TONE_WARNING)
            self._audit_label.setText("人工维护提示")
            self._refresh_audit_rows(issues)
        else:
            set_tone(self._audit_banner, TONE_SUCCESS)
            self._audit_label.setText("人工维护检查通过")
            self._refresh_audit_rows([])

    # ---------------------------------------------------------------
    # 审计提示行（逐条 + 跳转按钮）
    # ---------------------------------------------------------------
    # 特殊按钮文案；其余类型统一用「去检查」
    _ISSUE_BUTTON_TEXT = {
        "unclassified_hero": "去归类",
        "missing_settlement": "去补全",
    }

    def _refresh_audit_rows(self, issues: list[AuditIssue]) -> None:
        for row in self._audit_rows:
            row.setParent(None)
            row.deleteLater()
        self._audit_rows.clear()
        for issue in issues:
            row = self._build_audit_row(issue)
            self._audit_list_layout.addWidget(row)
            self._audit_rows.append(row)
        self._audit_list.setVisible(bool(issues))

    def _build_audit_row(self, issue: AuditIssue) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QLabel("· " + issue.message)
        label.setObjectName("noticeBannerMessage")
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        if issue.target_tab:
            button = QPushButton(self._ISSUE_BUTTON_TEXT.get(issue.kind, "去检查"))
            set_ui_role(button, ROLE_SECONDARY)
            button.clicked.connect(lambda _=False, iss=issue: self._jump_to_issue(iss))
            layout.addWidget(button)
        return row

    def _jump_to_issue(self, issue: AuditIssue) -> None:
        """按审计条目跳转到对应维护页签并定位目标数据。"""
        if issue.kind == "pending_refinement":
            self._open_refinement()
            return
        if not issue.target_tab:
            return
        for index in range(self._tabs.count()):
            if self._tabs.tabText(index) == issue.target_tab:
                self._tabs.setCurrentIndex(index)
                break
        kind = issue.kind
        if kind == "unclassified_hero":
            self._classification.focus_unclassified()
        elif kind in ("unknown_hero", "missing_settlement") and issue.target:
            self._special_cards.focus_item(*issue.target)

    def _open_refinement(self) -> None:
        dialog = IndexRefinementDialog(self._root / "data" / "rag_corpus", self)
        dialog.exec()
        self.refresh()

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