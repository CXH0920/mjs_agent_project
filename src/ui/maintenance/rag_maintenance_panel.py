# -*- coding: utf-8 -*-
"""知识库维护工作台（主导航第 4 页）。

布局重排后由 MaintenanceWorkspace 承载结构：左栏 10 项（5 个可编辑维护对象 +
5 个只读语料，状态点对齐语料任务状态）+ 右侧数据源工作区（复用现有 5 个面板）+
底部折叠执行日志。本面板只保留业务逻辑：语料状态计算、审计提示、索引精化与
本地一键执行 scripts/maintain_rag.py（ScriptRunner，实时输出日志）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from src.business.rag.audit_service import CORPUS_DIR, AuditIssue, audit_summary
from src.business.rag.hero_brief import load_hero_briefs
from src.business.rag.refinement_service import list_pending
from src.business.rag.task_defs import TASKS as _RAG_TASKS
from src.config.env import PROJECT_ROOT
from src.data.card_points_repository import CardPointsRepository
from src.data.equip_attrs_repository import EquipAttrsRepository
from src.data.hero_classification_repository import HeroClassificationRepository
from src.data.special_cards_repository import SpecialCardRepository
from src.ui.library.hero_classification_panel import HeroClassificationPanel
from src.ui.library.special_cards_panel import SpecialCardsPanel
from src.ui.maintenance.card_points_panel import CardPointsPanel
from src.ui.maintenance.equip_attrs_panel import EquipAttrsPanel
from src.ui.maintenance.index_refinement_dialog import IndexRefinementDialog
from src.ui.maintenance.maintenance_workspace import MaintenanceWorkspace
from src.ui.maintenance.rule_doc_panel import RuleDocPanel
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
from src.ui.shared.widgets import PageActionBar, ScriptRunner

# 语料任务：名称 -> (源文件, 输出语料文件)；定义与 scripts/maintain_rag.py 共用
# （单一事实源见 src/business/rag/task_defs.py，改动请同步维护该处）
TASK_DEFS: list[tuple[str, list[str], list[str]]] = [
    (task["name"], task["sources"], task["outputs"]) for task in _RAG_TASKS
]

# 左栏上组「维护对象」：(key/显示名, 语料任务名)；key 与 AuditIssue.target_tab
# 去掉「维护」后缀同名（如「武将分类维护」→「武将分类」），audit_service 无需改动
EDITABLE_SOURCE_ITEMS: list[tuple[str, str]] = [
    ("武将分类", "武将分类语料"),
    ("专属牌", "特殊机制语料"),
    ("卡牌点数", "点数花色语料"),
    ("装备属性", "装备属性语料"),
    ("元规则母本", "元规则/术语/FAQ"),
]

# 左栏下组「只读语料」：(key/显示名, 语料任务名)；数据源不在本模块内，仅状态与重建
READONLY_CORPUS_ITEMS: list[tuple[str, str]] = [
    ("武将语料", "武将语料"),
    ("卡牌语料", "卡牌语料"),
    ("加强削弱", "加强削弱语料"),
    ("组合语料", "组合语料"),
    ("武将攻略", "武将攻略语料"),
]
READONLY_TASK_BY_KEY = dict(READONLY_CORPUS_ITEMS)

PYTHON = sys.executable

# 语料块数缓存：path -> (mtime, size, count)，mtime/size 未变时不重复解析
_COUNT_CACHE: dict[str, tuple[float, int, int | None]] = {}


def _output_count(path: Path) -> int | None:
    """读取语料文件的块数（带 (mtime, size) 缓存，避免每次切页全量解析）。"""
    try:
        stat = path.stat()
    except OSError:
        return None
    cached = _COUNT_CACHE.get(str(path))
    if cached is not None and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        count = len(data) if isinstance(data, list) else None
    except (OSError, json.JSONDecodeError):
        count = None
    _COUNT_CACHE[str(path)] = (stat.st_mtime, stat.st_size, count)
    return count


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
            count = _output_count(root / CORPUS_DIR / outputs[0])
        rows.append({"name": name, "status": status, "count": count,
                     "sources": sources, "outputs": outputs})
    return rows


class RagMaintenancePanel(QWidget):
    """知识库维护工作台。"""

    data_changed = Signal()

    def __init__(self, root: Path = PROJECT_ROOT, hero_names: set[str] | None = None, parent=None):
        super().__init__(parent)
        self._root = root
        self._runner = ScriptRunner(self)
        self._runner.output.connect(self._append_log)
        self._runner.finished.connect(self._on_finished)
        self._hero_names, self._hero_positions, self._hero_skills = self._load_heroes(self._root, hero_names)
        self._setup_ui()
        self.refresh()

    @staticmethod
    def _load_heroes(root: Path, fallback: set[str] | None) -> tuple[set[str], dict[str, str], dict[str, str]]:
        """武将概要视图经业务层读取（技能文本格式归位 hero_brief，#A4）。"""
        return load_hero_briefs(root, fallback)

    def _setup_ui(self) -> None:
        self.setObjectName("ragMaintenancePanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

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
        # 执行类操作（单项重建入口收敛到左栏 ↻，此处保留全部粒度两种）
        self._corpus_button = QPushButton("重建全部语料")
        self._corpus_button.clicked.connect(lambda: self._run(["--force"]))
        self._action_bar.add_action(self._corpus_button, ROLE_SECONDARY)
        self._index_button = QPushButton("重建语料+索引")
        self._index_button.clicked.connect(lambda: self._run(["--force", "--build-index"]))
        self._action_bar.add_action(self._index_button, ROLE_PRIMARY)
        layout.addWidget(self._action_bar)

        # 审计提示条：跨数据源人工维护清单，有提示才出现（最多 3 条 + 折叠剩余）
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
        self._audit_banner.hide()
        layout.addWidget(self._audit_banner)

        # 工作台外壳：左栏导航 + 右侧面板（实例复用）+ 底部折叠日志
        self._workspace = MaintenanceWorkspace(self)
        self._log = self._workspace.log
        self._setup_sources()
        self._workspace.rebuild_requested.connect(
            lambda task: self._run(["--force", "--only", task]))
        self._workspace.meta_requested.connect(self._show_corpus_meta)
        layout.addWidget(self._workspace, 1)

    def _setup_sources(self) -> None:
        """创建 5 个现有面板实例并装配左栏两组导航项。"""
        self._rule_doc = RuleDocPanel(self._root)
        self._rule_doc.data_changed.connect(self._on_child_changed)
        # 元规则脚本输出汇入工作台底部日志（模块单一日志出口）
        self._rule_doc.script_started.connect(self._on_rule_doc_script_started)
        self._rule_doc.script_output.connect(self._on_rule_doc_output)
        self._rule_doc.script_finished.connect(self._on_rule_doc_script_finished)
        self._special_cards = SpecialCardsPanel(
            SpecialCardRepository(self._root / "data" / "special_cards.json"), self._hero_names)
        self._special_cards.data_changed.connect(self._on_child_changed)
        self._card_points = CardPointsPanel(
            CardPointsRepository(self._root / "data" / "card_points.json"), self._root)
        self._card_points.data_changed.connect(self._on_child_changed)
        self._equip_attrs = EquipAttrsPanel(
            EquipAttrsRepository(self._root / "data" / "equip_attrs.json"))
        self._equip_attrs.data_changed.connect(self._on_child_changed)
        self._classification = HeroClassificationPanel(
            HeroClassificationRepository(
                self._root / "data" / "hero_classification.json", self._hero_names),
            self._hero_positions, self._hero_skills)
        self._classification.data_changed.connect(self._on_child_changed)

        panels = {
            "武将分类": self._classification,
            "专属牌": self._special_cards,
            "卡牌点数": self._card_points,
            "装备属性": self._equip_attrs,
            "元规则母本": self._rule_doc,
        }
        self._workspace.add_group("维护对象", len(EDITABLE_SOURCE_ITEMS))
        for key, task_name in EDITABLE_SOURCE_ITEMS:
            self._workspace.add_source(key, task_name, panels[key])
        self._workspace.add_group("只读语料", len(READONLY_CORPUS_ITEMS))
        for key, task_name in READONLY_CORPUS_ITEMS:
            self._workspace.add_source(key, task_name)

    def _on_child_changed(self) -> None:
        """维护对象保存后：刷新语料状态（左栏状态点即时变化）并转发 data_changed。"""
        self.refresh()
        self.data_changed.emit()

    def _on_rule_doc_script_started(self) -> None:
        self._workspace.expand_log()
        self._workspace.set_log_meta("执行中…")

    def _on_rule_doc_output(self, data: bytes) -> None:
        text = bytes(data).decode("utf-8", errors="replace")
        self._workspace.on_log_output(text)
        self._workspace.log.appendPlainText(text.rstrip("\n"))

    def _on_rule_doc_script_finished(self, code: int) -> None:
        self._workspace.set_log_meta(f"退出码 {code}")

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
        # 索引精化入口：按钮带待精化数量角标；无待办时文案带 ✓ 但仍可进入浏览/管理已精化块
        pending = list_pending(self._root / "data" / "rag_corpus")
        pending_count = len(pending)
        self._refine_button.setText(f"索引精化（{pending_count}）" if pending_count else "索引精化 ✓")
        self._task_rows = {row["name"]: row for row in rows}
        self._workspace.set_task_states(self._task_rows)
        issues = audit_summary(self._root, pending)
        self._refresh_status_summary(rows, issues)
        self._refresh_audit_banner(issues)

    def _refresh_status_summary(self, rows: list[dict], issues: list[AuditIssue]) -> None:
        """全局操作栏状态摘要：语料 N 最新 · N 待重建 · 提示 N。"""
        fresh = sum(row["status"] == "最新" for row in rows)
        stale = sum(row["status"] == "待重建" for row in rows)
        missing = sum(row["status"] == "缺源" for row in rows)
        if stale or missing:
            parts = [f"语料 {fresh} 最新"]
            if stale:
                parts.append(f"{stale} 待重建")
            if missing:
                parts.append(f"{missing} 缺源")
            if issues:
                parts.append(f"提示 {len(issues)}")
            self._action_bar.set_status(" · ".join(parts), TONE_WARNING)
        else:
            summary = "所有语料与数据源一致"
            if issues:
                summary += f" · 提示 {len(issues)}"
            self._action_bar.set_status(summary, TONE_SUCCESS)

    # ---------------------------------------------------------------
    # 审计提示条（逐条 + 跳转按钮；最多 3 条，超出折叠为「还有 N 条」）
    # ---------------------------------------------------------------
    _MAX_AUDIT_ROWS = 3
    # 特殊按钮文案；其余类型统一用「去检查」
    _ISSUE_BUTTON_TEXT = {
        "unclassified_hero": "去归类",
        "missing_settlement": "去补全",
    }

    def _refresh_audit_banner(self, issues: list[AuditIssue]) -> None:
        if not issues:
            self._audit_banner.hide()
            return
        set_tone(self._audit_banner, TONE_WARNING)
        self._audit_label.setText("人工维护提示")
        hidden = len(issues) - self._MAX_AUDIT_ROWS
        for row in self._audit_rows:
            row.setParent(None)
            row.deleteLater()
        self._audit_rows.clear()
        for issue in issues[:self._MAX_AUDIT_ROWS]:
            row = self._build_audit_row(issue)
            self._audit_list_layout.addWidget(row)
            self._audit_rows.append(row)
        if hidden > 0:
            note = QLabel(f"还有 {hidden} 条提示，处理后点击「刷新状态」查看全部")
            note.setObjectName("noticeBannerMessage")
            self._audit_list_layout.addWidget(note)
            self._audit_rows.append(note)
        self._audit_list.setVisible(True)
        self._audit_banner.show()

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
        """按审计条目跳转到左栏对应维护对象并定位目标数据。

        AuditIssue.target_tab 仍是页签名（如「武将分类维护」），去掉「维护」
        后缀即左栏项 key；audit_service 侧无需改动。
        """
        if issue.kind == "pending_refinement":
            self._open_refinement()
            return
        if not issue.target_tab:
            return
        key = issue.target_tab.removesuffix("维护")
        if self._workspace.has_source(key):
            self._workspace.select_source(key)
        kind = issue.kind
        if kind == "unclassified_hero":
            self._classification.focus_unclassified()
        elif kind in ("unknown_hero", "missing_settlement") and issue.target:
            self._special_cards.focus_item(*issue.target)

    def _show_corpus_meta(self, key: str) -> None:
        """只读语料项点击：弹出该语料的元信息（不切右侧）。"""
        row = self._task_rows.get(READONLY_TASK_BY_KEY.get(key, ""))
        if row is None:
            return
        expected = next(
            (task.get("expected") for task in _RAG_TASKS if task["name"] == row["name"]), None)
        expect_text = "动态" if expected is None else str(expected)
        built = ""
        for rel in row["outputs"]:
            path = self._root / CORPUS_DIR / rel
            if path.exists():
                built = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
                break
        count = "—" if row["count"] is None else str(row["count"])
        QMessageBox.information(
            self, f"语料信息 · {key}",
            f"任务：{row['name']}\n状态：{row['status']}\n"
            f"输出块数：{count}（期望 {expect_text}）\n"
            f"来源文件：{'、'.join(row['sources'])}\n"
            f"上次构建：{built or '尚未构建'}",
        )

    def _open_refinement(self) -> None:
        dialog = IndexRefinementDialog(self._root / "data" / "rag_corpus", self)
        # 工作台内容的最小尺寸接近整屏，固定 1160x720 会在显示后被最小尺寸约束
        # 撑大（感知为"小窗一闪再变大"）；按主窗口大小预置尺寸，一次到位不跳变
        dialog.resize(self.window().size())
        dialog.exec()
        self.refresh()

    # ---------------------------------------------------------------
    # 本地执行（ScriptRunner 封装 QProcess）
    # ---------------------------------------------------------------
    def _run(self, args: list[str]) -> None:
        if self._runner.is_running():
            QMessageBox.information(self, "正在执行", "已有维护任务运行中，请等待完成。")
            return
        self._set_busy(True)
        self._workspace.expand_log()
        self._workspace.set_log_meta("执行中…")
        self._log.clear()
        self._log.appendPlainText("$ python -m src.scripts.maintain_rag " + " ".join(args))
        self._run_started = time.monotonic()
        self._runner.run(PYTHON, None, ['-m', 'src.scripts.maintain_rag'] + args, self._root)

    def _append_log(self, data: bytes) -> None:
        text = bytes(data).decode("utf-8", errors="replace")
        self._workspace.on_log_output(text)
        self._log.appendPlainText(text.rstrip("\n"))

    def _on_finished(self, code: int) -> None:
        self._set_busy(False)
        elapsed = time.monotonic() - getattr(self, "_run_started", time.monotonic())
        self._workspace.set_log_meta(f"退出码 {code} · {elapsed:.1f}s")
        if code == 0:
            self._log.appendPlainText("\n✔ 执行完成")
        else:
            self._log.appendPlainText(f"\n✘ 执行失败（退出码 {code}）")
        self.refresh()

    def _set_busy(self, busy: bool) -> None:
        # 执行期间一并禁用「索引精化」入口（#55）与左栏切换/单项重建
        for button in (self._refresh_button, self._corpus_button,
                       self._index_button, self._refine_button):
            button.setEnabled(not busy)
        self._workspace.set_interactive(not busy)
