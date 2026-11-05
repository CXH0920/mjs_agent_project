# -*- coding: utf-8 -*-
"""元规则维护面板（知识库维护 → 元规则母本）装配壳。

维护对象：docs/元规则整理-完整版.md（规则知识库 T0 母本，只增不删、机器校验）。
四个子页签（文档状态 audit / 数据段差异 sync / 提案工作台 propose / 疑难登记
pending）收敛在 src/ui/maintenance/rule_doc/ 下（第二轮第 15 批审查拆分），
本壳只保留：页签装配、脚本执行底座（ScriptRunner 互斥/哨兵码/ops 日志/输出
缓冲）与跨页签编排（audit→sync 串行、页签角标、下一步建议）。页签经意图
信号请求脚本执行，自身不感知脚本存在。
脚本输出经 script_output 信号转发到知识库维护工作台底部日志（模块单一日志出口）。
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget
from src.business.execution.script_runner import FAILED_TO_START_EXIT_CODE, ScriptRunner
from src.business.rag import rule_doc_service as rds
from src.ui.maintenance.rule_doc.audit_tab import AuditTab
from src.ui.maintenance.rule_doc.diff_tab import DiffTab
from src.ui.maintenance.rule_doc.pending_tab import PendingTab
from src.ui.maintenance.rule_doc.proposal_tab import ProposalTab
from src.ui.shared.style import ROLE_PRIMARY, TONE_INFO, TONE_SUCCESS, TONE_WARNING
from src.ui.shared.widgets import PageActionBar

PYTHON = sys.executable

# ---------------------------------------------------------------------------
# 执行记录日志（logs/rule_doc_ops.log）：用户操作轨迹，与 app.log（内部异常）分离
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)
_OPS_LOGGER = logging.getLogger("rule_doc_ops")
_OPS_LOGGER.propagate = False
_OPS_HANDLER_ROOT: Path | None = None


def _ensure_ops_handler(root: Path) -> None:
    """按项目根注册 rule_doc_ops 文件 handler（root 变化时换绑，防重复注册）。"""
    global _OPS_HANDLER_ROOT
    if _OPS_HANDLER_ROOT == root:
        return
    if _OPS_HANDLER_ROOT is not None:
        for handler in _OPS_LOGGER.handlers[:]:
            _OPS_LOGGER.removeHandler(handler)
            handler.close()
    log_path = root / "logs" / "rule_doc_ops.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    handler.setLevel(logging.INFO)
    _OPS_LOGGER.addHandler(handler)
    _OPS_LOGGER.setLevel(logging.INFO)
    _OPS_HANDLER_ROOT = root


class RuleDocPanel(QWidget):
    """元规则母本维护装配壳：四页签 + 脚本执行底座 + 跨页签编排。"""

    data_changed = Signal()
    # 脚本执行转发：输出汇入知识库维护工作台底部日志（模块单一日志出口）
    script_started = Signal()
    script_output = Signal(bytes)
    script_finished = Signal(int)

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self._root = root
        self._runner = ScriptRunner(self)
        self._runner.output.connect(self._append_log)
        self._runner.finished.connect(self._on_runner_finished)
        self._pending_finished: tuple = ()  # (on_finished, sentinel_codes, sentinel_note, failure_codes)
        # 最近一次脚本运行是否有业务产物可解析（0/哨兵码/业务失败码为 True）；
        # 崩溃/启动失败时回调不得把空输出解析成结论（见 _on_audit_done）
        self._last_run_ok = True
        self._pending_sync = False
        # 尚未完成 audit+sync 检查（A2 未检查引导）
        self._checked = False
        # 脚本输出缓冲：替代原内嵌日志控件，供 _last_output() 解析与测试注入
        self._output_text = ""
        self._last_command = ""
        _ensure_ops_handler(root)
        self._setup_ui()
        self.refresh()

    # ---------------------------------------------------------------
    # UI 装配（页签本体在 rule_doc/ 子包）
    # ---------------------------------------------------------------
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._action_bar = PageActionBar()
        self._refresh_button = QPushButton("刷新状态")
        self._refresh_button.setToolTip(
            "维护规则母本 docs/元规则整理-完整版.md（只增不删、机器校验）；"
            "依次完成：检查 → 应用差异 → 提案 → 登记疑难")
        self._refresh_button.clicked.connect(self.refresh_all)
        self._action_bar.add_action(self._refresh_button, ROLE_PRIMARY)
        layout.addWidget(self._action_bar)

        # 四个工作流各为一个子页签，页签经意图信号请求脚本执行/跨页联动
        self._tabs = QTabWidget()
        self._tabs.setObjectName("sectionTabs")
        self._audit_tab = AuditTab(self._root, self)
        self._audit_tab.sync_requested.connect(self._jump_to_diff)
        self._tabs.addTab(self._audit_tab, "文档状态")
        self._diff_tab = DiffTab(self._root, self)
        self._diff_tab.apply_requested.connect(self._run_apply_json)
        self._tabs.addTab(self._diff_tab, "数据段差异")
        self._proposal_tab = ProposalTab(self._root, self)
        self._proposal_tab.generate_requested.connect(self._generate_proposal)
        self._proposal_tab.apply_requested.connect(self._apply_proposal)
        self._tabs.addTab(self._proposal_tab, "提案工作台")
        self._pending_tab = PendingTab(self._root, self)
        self._pending_tab.proposal_created.connect(self._on_pending_converted)
        self._tabs.addTab(self._pending_tab, "疑难登记")
        layout.addWidget(self._tabs, 1)

    # ---------------------------------------------------------------
    # 刷新编排（本地只读解析，不发进程）
    # ---------------------------------------------------------------
    def refresh(self) -> None:
        """本地只读刷新（提案/疑难/差异表格），不启动进程。"""
        self._proposal_tab.refresh_proposals()
        self._pending_tab.refresh()
        self._proposal_tab.load_detail()
        if not self._checked:
            # A2：打开页面尚未检查时给出明确的第一步指引
            self._action_bar.set_status("尚未检查，点击「刷新状态」开始检查文档", TONE_INFO)

    def refresh_all(self) -> None:
        """串行刷新：先 audit，完成后 sync（避免并发 QProcess 冲突）。"""
        pending = self._diff_tab.unsaved_edit_count()
        if pending:
            # 勾选与「确认值」都是用户输入，audit+sync 完成后差异表整表重建会
            # 丢弃；刷新前必须确认（与编辑器类控件的脏确认同等保护）
            answer = QMessageBox.question(
                self, "未应用的差异编辑",
                "检测到 %d 处未应用的勾选/确认值修改，刷新后将被丢弃。仍要刷新？" % pending,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._pending_sync = True
        self.refresh_audit()

    def reload_data(self) -> None:
        self.refresh()

    def refresh_audit(self) -> None:
        self._run_script(["audit_rule_doc.py"], self._on_audit_done)

    def refresh_diffs(self) -> None:
        json_path = rds.sync_json_path(self._root)
        # 退出码 1 = 检测到差异（脚本哨兵语义，非失败）
        self._run_script(["sync_rule_stats.py", "--json", str(json_path)], self._on_sync_done,
                         sentinel_codes={1}, sentinel_note="检测到差异，见差异表")

    # ---------------------------------------------------------------
    # 各命令完成回调（结果渲染委托页签，壳负责角标/串接/下一步）
    # ---------------------------------------------------------------
    def _on_audit_done(self) -> None:
        self._audit_tab.apply_audit_result(self._last_run_ok, self._last_output())
        if not self._last_run_ok:
            # 脚本崩溃/启动失败：不设角标、不串行 sync（保持上次结果）
            return
        # A4 页签角标：文档状态（ERROR+WARN 数）
        counts = self._audit_tab.counts
        badge = counts["ERROR"] + counts["WARN"]
        self._tabs.setTabText(0, "文档状态" if not badge else f"文档状态（{badge}）")
        if self._pending_sync:
            self._pending_sync = False
            self.refresh_diffs()

    def _on_sync_done(self) -> None:
        if not self._last_run_ok:
            # sync 崩溃时磁盘上的 json 是上一轮旧产物，解析会展示过期差异
            return
        self._checked = True
        self._diff_tab.apply_diff_result()
        # A4 页签角标：数据段差异（条数）
        n = len(self._diff_tab.diffs)
        self._tabs.setTabText(1, "数据段差异" if not n else f"数据段差异（{n}）")
        # A3 状态驱动"下一步建议"
        status, tone = self._compose_next_step(self._diff_tab.diffs)
        self._action_bar.set_status(status, tone)

    def _on_apply_json_done(self) -> None:
        """应用完成后重跑 --json 刷新差异表（成功行消失，失败行保留）。"""
        self.refresh_diffs()

    def _on_generate_done(self) -> None:
        self._proposal_tab.refresh_proposals()

    def _on_apply_proposal_done(self) -> None:
        self._proposal_tab.refresh_proposals()

    def _on_pending_converted(self) -> None:
        """疑难转提案：刷新提案工作台清单。"""
        self._proposal_tab.refresh_proposals()

    def _jump_to_diff(self) -> None:
        """audit 明细「去同步」：切到数据段差异页签。"""
        self._tabs.setCurrentIndex(self._tabs.indexOf(self._diff_tab))

    def _compose_next_step(self, diffs: list[dict]) -> tuple[str, str]:
        """按审计/差异/提案/疑难状态给出下一步建议（A3）。"""
        counts = self._audit_tab.counts
        full = sum(1 for d in diffs if d["kind"] == "full")
        candidate = sum(1 for d in diffs if d["kind"] == "candidate")
        checkpoint = sum(1 for d in diffs if d["kind"] == "checkpoint")
        if counts["ERROR"]:
            return "文档校验有 ERROR，请查看「文档状态」", TONE_WARNING
        if full:
            return f"全自动差异 {full} 处可一键应用（数据段差异页）", TONE_WARNING
        if counts["WARN"]:
            return "文档校验有告警，查看「文档状态」", TONE_WARNING
        if candidate:
            return f"候选差异 {candidate} 处需人工确认（数据段差异页）", TONE_WARNING
        if checkpoint:
            return f"校验点 {checkpoint} 处需人工核对（数据段差异页）", TONE_WARNING
        if self._proposal_tab.pending_count:
            return f"提案 {self._proposal_tab.pending_count} 条待确认（提案工作台）", TONE_WARNING
        if self._pending_tab.open_count:
            return f"疑难 {self._pending_tab.open_count} 条待消化（疑难登记）", TONE_WARNING
        return "校验通过、数据一致、无待办", TONE_SUCCESS

    # ---------------------------------------------------------------
    # 脚本执行（ScriptRunner 封装 QProcess；页签的意图信号在此启动）
    # ---------------------------------------------------------------
    def _generate_proposal(self) -> None:
        self._run_script(["propose_rule_changes.py", "--no-llm"], self._on_generate_done)

    def _apply_proposal(self, path: str) -> None:
        self._run_script(["apply_rule_proposal.py", "--proposal", str(path)],
                         self._on_apply_proposal_done)

    def _run_apply_json(self, path) -> None:
        self._run_script(
            ["sync_rule_stats.py", "--apply-json", str(path)], self._on_apply_json_done,
            failure_codes={
                1: "应用失败：文档可能已被修改，未写入（见上方明细，可刷新后重试）",
                2: "前置失败：确认清单或文档不可读，未写入",
            })

    def _run_script(self, args: list[str], on_finished,
                    sentinel_codes: set[int] | None = None,
                    sentinel_note: str = "",
                    failure_codes: dict[int, str] | None = None) -> None:
        """启动脚本；sentinel_codes 为"业务性非零退出码"（如 sync 的 1=有差异），
        failure_codes 为"业务失败但需定制文案"的退出码映射。
        """
        if self._runner.is_running():
            QMessageBox.information(self, "正在执行", "已有任务运行中，请等待完成。")
            return
        # 清空上一轮输出缓冲，否则 _on_audit_done 解析到累积的多轮 WARN 行，
        # 计数/明细/角标全部翻倍
        self._clear_script_output()
        module = 'src.scripts.' + args[0].removesuffix('.py')
        self._last_command = " ".join(args)
        self.script_started.emit()
        self._emit_output("$ python -m " + module + " " + " ".join(args[1:]))
        self._pending_finished = (on_finished, sentinel_codes, sentinel_note, failure_codes)
        if self._runner.run(PYTHON, None, ['-m', module] + args[1:], self._root) is False:
            # 启动失败不再走 finished（避免与 run() 返回值抢语义），
            # 这里直接按失败收尾，否则面板永久停在"执行中"
            self._on_finished(
                FAILED_TO_START_EXIT_CODE, None,
                on_finished, sentinel_codes, sentinel_note, failure_codes,
            )
            self.script_finished.emit(FAILED_TO_START_EXIT_CODE)

    def _on_runner_finished(self, code: int) -> None:
        """ScriptRunner 完成回调：转发到原 _on_finished（保留签名供测试直接调用）。"""
        self._on_finished(code, None, *self._pending_finished)
        self.script_finished.emit(code)

    def _append_log(self, data: bytes) -> None:
        text = bytes(data).decode("utf-8", errors="replace")
        self._emit_output(text.rstrip("\n"))

    def _emit_output(self, text: str) -> None:
        """追加脚本输出：写入缓冲并转发到工作台底部日志。"""
        if self._output_text:
            self._output_text += "\n"
        self._output_text += text
        self.script_output.emit(text.encode("utf-8"))

    def _clear_script_output(self) -> None:
        """清空输出缓冲（语义等同原内嵌日志控件 clear）。"""
        self._output_text = ""

    def _append_marked(self, text: str, kind: str = "default") -> None:
        # 结论行经工作台日志展示：纯文本，✔/⚠/✘ 前缀承载语义
        self._emit_output(text)

    def _on_finished(self, code: int, _status, on_finished,
                     sentinel_codes: set[int] | None = None,
                     sentinel_note: str = "",
                     failure_codes: dict[int, str] | None = None) -> None:
        if code == 0:
            conclusion, kind = "✔ 执行完成", "success"
        elif sentinel_codes and code in sentinel_codes:
            note = ("：" + sentinel_note) if sentinel_note else ""
            conclusion, kind = "⚠ 执行完成（退出码 %d%s）" % (code, note), "warning"
        elif failure_codes and code in failure_codes:
            conclusion, kind = "✘ " + failure_codes[code], "error"
        else:
            conclusion, kind = "✘ 执行失败（退出码 %d）" % code, "error"
        self._append_marked(conclusion, kind)
        # 执行记录持久化（rule_doc_ops.log）：用户操作轨迹
        _OPS_LOGGER.info("%s → exit=%d %s", self._last_command, code, conclusion)
        # 非 0 且非哨兵/业务失败码 = 崩溃或启动失败，输出无业务产物；
        # 业务失败码（如 --apply-json 的 1/2）有定制文案且回调仍需刷新状态
        self._last_run_ok = (
            code == 0
            or bool(sentinel_codes and code in sentinel_codes)
            or bool(failure_codes and code in failure_codes)
        )
        try:
            on_finished()
        except Exception as exc:
            logger.exception("脚本完成回调处理失败")
            self._emit_output("刷新失败：%s" % exc)
        self.data_changed.emit()

    def _last_output(self) -> str:
        return self._output_text
