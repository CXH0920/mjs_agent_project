# -*- coding: utf-8 -*-
"""知识库维护工作台（主导航第 4 页）。

布局重排后由 MaintenanceWorkspace 承载结构：左栏 11 项（5 个可编辑维护对象 +
5 个只读语料 + 1 个向量索引，状态点对齐语料任务/索引状态）+ 右侧数据源工作区
（复用现有 5 个面板）+ 底部折叠执行日志。本面板只保留业务逻辑：语料与索引
状态计算、审计提示、索引精化与本地一键执行 scripts/maintain_rag.py /
src.rag.indexer（ScriptRunner，实时输出日志；面板只显示当前执行，历史留
内存快照经下拉弹窗回看）、只读语料内容查看（readonly_viewer_dialog）。
"""

from __future__ import annotations

import json
import re
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
from src.business.execution.script_runner import ScriptRunner
from src.business.maintenance.maintenance_repositories import MaintenanceRepositories
from src.business.rag.audit_service import CORPUS_DIR, AuditIssue, audit_summary
from src.business.rag.corpus_freshness import (  # noqa: F401 — re-export，模块级名字保持兼容
    INDEX_DIR,
    INDEX_TASK_NAME,
    index_state,
    task_states,
)
from src.business.rag.hero_brief import load_hero_briefs
from src.business.rag.refinement_service import list_pending
from src.business.rag.task_defs import TASKS as _RAG_TASKS
from src.config.env import PROJECT_ROOT
from src.ui.maintenance.card_points_panel import CardPointsPanel
from src.ui.maintenance.equip_attrs_panel import EquipAttrsPanel
from src.ui.maintenance.hero_classification_panel import HeroClassificationPanel
from src.ui.maintenance.index_refinement_dialog import IndexRefinementDialog
from src.ui.maintenance.maintenance_workspace import MaintenanceWorkspace
from src.ui.maintenance.readonly_viewer_dialog import ReadonlyViewerDialog
from src.ui.maintenance.rule_doc_panel import RuleDocPanel
from src.ui.maintenance.special_cards_panel import SpecialCardsPanel
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

# 语料任务/索引新鲜度判定（TASK_DEFS、task_states、INDEX_*、index_state、块数缓存）
# 已下沉 src/business/rag/corpus_freshness.py（P0-2：采集完成提醒/芯片菜单/生成
# 预检多方共用的单一事实源），顶部 import 兼容 re-export 保持模块级名字不变。

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

# 执行历史内存快照上限（条数丢最旧 / 单次正文字符截断防长会话膨胀）
_MAX_LOG_RUNS = 10
_MAX_RUN_TEXT_CHARS = 100_000

# 元规则脚本名 → 历史条目友好名（解析快照正文首行的 "$ python -m ..." 命令）
_RULE_DOC_SCRIPT_LABELS = {
    "audit_rule_doc": "元规则文档检查",
    "sync_rule_stats": "元规则数据同步",
    "propose_rule_changes": "生成元规则提案",
    "apply_rule_proposal": "合入元规则提案",
}


class RagMaintenancePanel(QWidget):
    """知识库维护工作台。"""

    # 预留信号：维护对象保存后发射。当前全仓无消费方——维护页 5 个数据文件
    # 在 UI 展示层零消费，状态视图已由 _on_child_changed 的 refresh() 兜底。
    # 未来消费者方向：状态栏「语料待重建」类跨页联动，接线时在组合根
    # （main_window）桥接，先例见 synergies_changed（main_window.py 顶部连接区）。
    data_changed = Signal()

    def __init__(self, root: Path = PROJECT_ROOT, hero_names: set[str] | None = None,
                 *, repositories: MaintenanceRepositories, parent=None):
        super().__init__(parent)
        self._root = root
        self._repositories = repositories
        self._runner = ScriptRunner(self)
        self._runner.output.connect(self._append_log)
        self._runner.finished.connect(self._on_finished)
        self._hero_names, self._hero_positions, self._hero_skills = self._load_heroes(self._root, hero_names)
        # 合并后的名单回填 classification 仓储（原直接以 self._hero_names 构造仓储的语义）
        self._repositories.classification.update_hero_names(self._hero_names)
        # 执行历史内存快照（会话内）：底部日志面板只显示当前执行，历史经下拉弹窗回看
        self._log_runs: list[dict] = []
        self._active_run: dict | None = None
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
        # 检索诊断：只读工具，定位语料/索引变动前后的召回差异（G5）
        self._diagnose_button = QPushButton("检索诊断")
        self._diagnose_button.clicked.connect(self._open_diagnostic)
        self._action_bar.add_action(self._diagnose_button, ROLE_SECONDARY)
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

        # 审计提示条：跨数据源人工维护清单，有提示才出现（默认 3 条，可展开全部）
        self._banner_expanded = False  # 展开/收起跨刷新保留（处理中不被折叠回去）
        self._audit_issues: list[AuditIssue] = []  # 最近一次清单（切换按钮重渲染用）
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
        self._workspace.rebuild_requested.connect(self._on_rebuild_requested)
        self._workspace.meta_requested.connect(self._show_corpus_meta)
        self._workspace.history_selected.connect(self._show_log_run)
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
            self._repositories.special_cards, self._hero_names,
            root=self._root)
        self._special_cards.data_changed.connect(self._on_child_changed)
        self._card_points = CardPointsPanel(
            self._repositories.card_points, self._root)
        self._card_points.data_changed.connect(self._on_child_changed)
        self._equip_attrs = EquipAttrsPanel(
            self._repositories.equip_attrs)
        self._equip_attrs.data_changed.connect(self._on_child_changed)
        self._classification = HeroClassificationPanel(
            self._repositories.classification,
            self._hero_positions, self._hero_skills, root=self._root)
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
        self._workspace.add_group("语料与索引", len(READONLY_CORPUS_ITEMS) + 1)
        for key, task_name in READONLY_CORPUS_ITEMS:
            self._workspace.add_source(key, task_name)
        # 向量索引非语料任务：只读行 + 待同步时 ↻ 走 indexer（见 _on_rebuild_requested）
        self._workspace.add_source(INDEX_TASK_NAME, INDEX_TASK_NAME)

    def _on_rebuild_requested(self, task: str) -> None:
        """左栏 ↻ 分派：语料任务走 maintain_rag --only，向量索引走 indexer。"""
        if task == INDEX_TASK_NAME:
            self._run_index()
        else:
            self._run(["--force", "--only", task])

    def _on_child_changed(self) -> None:
        """维护对象保存后：刷新语料状态（左栏状态点即时变化）并转发 data_changed。"""
        self.refresh()
        self.data_changed.emit()

    def _on_rule_doc_script_started(self) -> None:
        self._workspace.expand_log()
        self._workspace.set_log_meta("执行中…")
        # 命令行此刻拿不到（script_started 无参），封口时从快照正文首行解析
        self._begin_log_run("元规则脚本")

    def _on_rule_doc_output(self, data: bytes) -> None:
        text = bytes(data).decode("utf-8", errors="replace")
        self._workspace.on_log_output(text)
        self._workspace.log.appendPlainText(text.rstrip("\n"))
        self._append_log_run_text(text)

    def _on_rule_doc_script_finished(self, code: int) -> None:
        self._workspace.set_log_meta(f"退出码 {code}")
        self._finish_log_run(code, None)

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
        # 向量索引状态并入左栏（set_task_states 按任务名匹配，同 dict 机制）
        self._task_rows[INDEX_TASK_NAME] = index_state(self._root)
        self._workspace.set_task_states(self._task_rows)
        issues = audit_summary(self._root, pending)
        self._refresh_status_summary(rows, issues, self._task_rows[INDEX_TASK_NAME])
        self._refresh_audit_banner(issues)

    def _refresh_status_summary(self, rows: list[dict], issues: list[AuditIssue],
                                index_row: dict) -> None:
        """全局操作栏状态摘要：语料 N 最新 · N 待重建 · 提示 N · 索引状态。"""
        fresh = sum(row["status"] == "最新" for row in rows)
        stale = sum(row["status"] == "待重建" for row in rows)
        missing = sum(row["status"] == "缺源" for row in rows)
        index_stale = index_row["status"] != "最新"
        if stale or missing or index_stale:
            parts = [f"语料 {fresh} 最新"]
            if stale:
                parts.append(f"{stale} 待重建")
            if missing:
                parts.append(f"{missing} 缺源")
            if issues:
                parts.append(f"提示 {len(issues)}")
            if index_stale:
                parts.append(f"索引{index_row['status']}")
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
        "curated_stale": "去复核",
        "curated_stale_possible": "去复核",
        "card_curated_stale": "去复核",
    }

    def _refresh_audit_banner(self, issues: list[AuditIssue]) -> None:
        self._audit_issues = issues
        if not issues:
            self._audit_banner.hide()
            return
        set_tone(self._audit_banner, TONE_WARNING)
        self._audit_label.setText("人工维护提示")
        for row in self._audit_rows:
            row.setParent(None)
            row.deleteLater()
        self._audit_rows.clear()
        shown = issues if self._banner_expanded else issues[:self._MAX_AUDIT_ROWS]
        for issue in shown:
            row = self._build_audit_row(issue)
            self._audit_list_layout.addWidget(row)
            self._audit_rows.append(row)
        if len(issues) > self._MAX_AUDIT_ROWS:
            toggle = QPushButton(
                "收起" if self._banner_expanded
                else f"展开全部（还有 {len(issues) - self._MAX_AUDIT_ROWS} 条）")
            set_ui_role(toggle, ROLE_SECONDARY)
            toggle.clicked.connect(self._toggle_audit_banner)
            self._audit_list_layout.addWidget(toggle)
            self._audit_rows.append(toggle)
        self._audit_list.setVisible(True)
        self._audit_banner.show()

    def _toggle_audit_banner(self) -> None:
        """展开/收起全部审计提示（按钮行随 _audit_rows 重建自回收）。"""
        self._banner_expanded = not self._banner_expanded
        self._refresh_audit_banner(self._audit_issues)

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
        if issue.kind in ("pending_refinement", "curated_stale", "curated_stale_possible",
                          "card_curated_stale"):
            # 时效类条目携带结构化命中清单（target），跳转时定位到第一个过期块
            self._open_refinement(issue.target)
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
        """只读语料项点击：弹出该语料的元信息与内容查看器（不切右侧）。"""
        if key == INDEX_TASK_NAME:
            self._show_index_meta()
            return
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
        body, note, body_meta = self._load_corpus_text(row, key)
        meta_text = (
            f"任务：{row['name']}｜状态：{row['status']}｜输出块数：{count}（期望 {expect_text}）\n"
            f"来源文件：{'、'.join(row['sources'])}\n"
            f"上次构建：{built or '尚未构建'}{body_meta}"
        )
        dialog = ReadonlyViewerDialog(
            f"语料内容 · {key}", row["name"], meta_text, body, note=note, parent=self)
        dialog.exec()

    def _load_corpus_text(self, row: dict, key: str) -> tuple[str, str, str]:
        """读语料正文：优先同名 .md 伴文件（与 json 同次构建生成），缺失降级 json 摘要。

        返回 (body, note, body_meta)；武将攻略语料的 .md 构建时按块截断至 150 字
        （build_guide_corpus.py），如实标注不冒充全量。
        """
        rel = row["outputs"][0]
        md_path = self._root / CORPUS_DIR / (rel.rsplit(".", 1)[0] + ".md")
        try:
            body = md_path.read_text(encoding="utf-8")
        except OSError:
            return self._summarize_corpus_json(rel)
        note = "本语料正文为前 150 字预览（构建时截断），完整内容见同名 json" if key == "武将攻略" else ""
        return body, note, f"\n正文：{len(body)} 字符（{md_path.name}）"

    def _summarize_corpus_json(self, rel: str) -> tuple[str, str, str]:
        """.md 伴文件缺失/不可读时的降级：json 前 50 块的 block_id 摘要。"""
        path = self._root / CORPUS_DIR / rel
        try:
            blocks = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return f"（正文不可读：{error}）", "", "\n正文：读取失败"
        lines = [
            f"{block.get('block_id', '?')}｜"
            f"{block.get('name') or block.get('hero') or block.get('skill') or ''}"
            for block in blocks[:50] if isinstance(block, dict)
        ]
        body = "\n".join(lines) or "（语料为空）"
        return body, "", "\n正文：json 前 50 块摘要（.md 伴文件缺失）"

    def _show_index_meta(self) -> None:
        """向量索引左栏行点击：弹索引元信息与段目录文件清单。"""
        row = self._task_rows.get(INDEX_TASK_NAME)
        if row is None:
            return
        built = (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["built"]))
                 if row["built"] else "尚未构建")
        seg_root = self._root / INDEX_DIR
        files: list[str] = []
        total = 0
        if seg_root.is_dir():
            for sub in sorted(seg_root.iterdir()):
                if not sub.is_dir():
                    continue
                for file in sorted(sub.iterdir()):
                    if file.is_file():
                        size = file.stat().st_size
                        total += size
                        files.append(f"{sub.name}/{file.name}  {size / 1024:.1f} KB")
        meta_text = (f"状态：{row['status']}｜上次构建：{built}\n"
                     f"目录：{seg_root}｜总大小：{total / 1024 / 1024:.1f} MB")
        dialog = ReadonlyViewerDialog(
            "向量索引", "检索用向量库（chroma）", meta_text,
            "\n".join(files) if files else "（索引目录为空）", parent=self)
        dialog.exec()

    def _open_refinement(self, focus: object = None) -> None:
        """打开索引精化弹窗；focus 为时效命中清单时定位并可逐站循环（P1-1）。"""
        dialog = IndexRefinementDialog(
            self._root / "data" / "rag_corpus", self,
            focus_queries=self._refinement_focus_queries(focus))
        # 工作台内容的最小尺寸接近整屏，固定 1160x720 会在显示后被最小尺寸约束
        # 撑大（感知为"小窗一闪再变大"）；按主窗口大小预置尺寸，一次到位不跳变
        dialog.resize(self.window().size())
        dialog.exec()
        self.refresh()

    @staticmethod
    def _refinement_focus_queries(focus: object) -> list[str]:
        """时效命中清单 → 定位搜索词列表（去重保序），供弹窗逐站导航。

        同武将多块（hero 级命中）只留一站；清单为空或结构异常返回空列表
        （弹窗按普通方式打开，无导航行）。
        """
        if not focus or not isinstance(focus, list):
            return []
        queries = []
        for hit in focus:
            if isinstance(hit, dict):
                query = str(hit.get("skill") or hit.get("hero") or hit.get("card") or "")
                if query:
                    queries.append(query)
        return list(dict.fromkeys(queries))

    @staticmethod
    def _refinement_focus_query(focus: object) -> str:
        """时效命中清单 → 首个定位搜索词（技能名优先，回退武将/卡牌名）。"""
        queries = RagMaintenancePanel._refinement_focus_queries(focus)
        return queries[0] if queries else ""

    def _open_diagnostic(self) -> None:
        from src.ui.maintenance.retrieval_diagnostic_dialog import (
            RetrievalDiagnosticDialog,
        )

        dialog = RetrievalDiagnosticDialog(self)
        dialog.exec()

    # ---------------------------------------------------------------
    # 本地执行（ScriptRunner 封装 QProcess）
    # ---------------------------------------------------------------
    def _run(self, args: list[str]) -> None:
        self._run_module("src.scripts.maintain_rag", args)

    def _run_index(self) -> None:
        """全量重建向量索引（分钟级，含编码器加载与全量重嵌入）。"""
        self._run_module("src.rag.indexer", [])

    def _run_module(self, module: str, extra_args: list[str]) -> None:
        if self._runner.is_running():
            QMessageBox.information(self, "正在执行", "已有维护任务运行中，请等待完成。")
            return
        self._set_busy(True)
        self._workspace.expand_log()
        self._workspace.set_log_meta("执行中…")
        self._begin_log_run(self._friendly_label(module, extra_args))
        command_line = "$ python -m " + module + " " + " ".join(extra_args)
        self._active_run["text"] = command_line  # 快照首行即命令行（路②经输出转发自然带入）
        self._log.clear()
        self._log.appendPlainText(command_line)
        self._run_started = time.monotonic()
        if self._runner.run(PYTHON, None, ['-m', module] + extra_args, self._root) is False:
            # 启动失败不再走 finished（避免与 run() 返回值抢语义），
            # 忙状态必须在此复位，否则整页按钮永久禁用
            self._set_busy(False)
            self._workspace.set_log_meta("启动失败")
            self._log.appendPlainText("\n✘ 任务启动失败，请查看日志")
            self._finish_log_run(-1, None)

    def _append_log(self, data: bytes) -> None:
        text = bytes(data).decode("utf-8", errors="replace")
        self._workspace.on_log_output(text)
        self._log.appendPlainText(text.rstrip("\n"))
        self._append_log_run_text(text)

    def _on_finished(self, code: int) -> None:
        self._set_busy(False)
        elapsed = time.monotonic() - getattr(self, "_run_started", time.monotonic())
        self._workspace.set_log_meta(f"退出码 {code} · {elapsed:.1f}s")
        if code == 0:
            self._log.appendPlainText("\n✔ 执行完成")
        else:
            self._log.appendPlainText(f"\n✘ 执行失败（退出码 {code}）")
        self._finish_log_run(code, elapsed)
        self.refresh()

    # ---------------------------------------------------------------
    # 执行历史快照（会话内）：面板只显示当前执行，历史经下拉弹窗回看
    # ---------------------------------------------------------------
    @staticmethod
    def _friendly_label(module: str, args: list[str]) -> str:
        """路①命令 → 历史条目友好名（与操作栏/左栏 ↻ 的动作名对齐）。"""
        if module.endswith("indexer"):
            return "重建向量索引"
        if "--build-index" in args:
            return "重建语料+索引"
        if "--only" in args:
            return f"重建{args[args.index('--only') + 1]}"
        return "语料维护"

    @staticmethod
    def _rule_doc_script_label(text: str) -> str:
        """路②快照正文首行（"$ python -m src.scripts.<脚本>.py …"）→ 友好名。"""
        match = re.search(r"(\w+)\.py", text.splitlines()[0] if text else "")
        if match:
            return _RULE_DOC_SCRIPT_LABELS.get(match.group(1), "元规则脚本")
        return "元规则脚本"

    def _begin_log_run(self, label: str) -> None:
        run = {"label": label, "started": time.strftime("%m-%d %H:%M:%S"),
               "code": None, "elapsed": None, "text": ""}
        self._log_runs.append(run)
        del self._log_runs[:-_MAX_LOG_RUNS]  # 丢最旧
        self._active_run = run
        self._sync_history_combo()

    def _append_log_run_text(self, text: str) -> None:
        if self._active_run is None:
            return
        merged = self._active_run["text"] + text
        self._active_run["text"] = merged[:_MAX_RUN_TEXT_CHARS]

    def _finish_log_run(self, code: int, elapsed: float | None) -> None:
        run, self._active_run = self._active_run, None
        if run is None:
            return
        run["code"], run["elapsed"] = code, elapsed
        if run["label"] == "元规则脚本":
            run["label"] = self._rule_doc_script_label(run["text"])
        if elapsed is not None:
            run["label"] += f" ✔ {elapsed:.1f}s" if code == 0 else f" ✘ {elapsed:.1f}s"
        else:
            run["label"] += " ✔" if code == 0 else " ✘"
        self._sync_history_combo()

    def _sync_history_combo(self) -> None:
        """倒序喂给历史下拉（最新在上，userData 为快照索引）。"""
        self._workspace.set_log_history(
            [(run["label"], index) for index, run in reversed(list(enumerate(self._log_runs)))])

    def _show_log_run(self, index) -> None:
        """历史下拉选中：弹只读查看器显示该次执行的完整输出。"""
        if not isinstance(index, int) or not 0 <= index < len(self._log_runs):
            return
        run = self._log_runs[index]
        first_line = run["text"].splitlines()[0] if run["text"] else "（无输出）"
        code_text = "执行中…" if run["code"] is None else f"退出码 {run['code']}"
        elapsed_text = "" if run["elapsed"] is None else f" · {run['elapsed']:.1f}s"
        dialog = ReadonlyViewerDialog(
            "执行历史", run["label"],
            f"{first_line}\n开始 {run['started']} · {code_text}{elapsed_text}",
            run["text"], parent=self)
        dialog.exec()

    def _set_busy(self, busy: bool) -> None:
        # 执行期间一并禁用「索引精化」入口（#55）与左栏切换/单项重建
        for button in (self._refresh_button, self._corpus_button,
                       self._index_button, self._refine_button):
            button.setEnabled(not busy)
        self._workspace.set_interactive(not busy)
