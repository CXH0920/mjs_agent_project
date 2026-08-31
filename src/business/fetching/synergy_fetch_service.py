"""
名将杀 Agent - 相性获取业务服务

负责编排 AI 相性评分生成流程，管理 QProcess 生命周期。
支持选定武将（单武将 x 全体）和指定获取（两个武将配对）两种模式。
"""

from __future__ import annotations

import json
import logging
import re
import tempfile

from PySide6.QtCore import Signal

from src.business.fetching.base_fetch_service import BaseFetchService
from src.business.fetching.fetch_utils import is_generation_progress_line

logger = logging.getLogger(__name__)


class SynergyFetchService(BaseFetchService):
    """相性获取业务服务"""

    progress_output = Signal(str)        # 原始 stdout 行
    progress_value = Signal(int, int)    # (current, total) 供进度条使用
    fetch_completed = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)

    @property
    def _service_name(self) -> str:
        return "相性计算"

    @property
    def _subprocess_log_namespace(self) -> str:
        return "subprocess.ai"

    # ---------------------------------------------------------------
    # 公共接口
    # ---------------------------------------------------------------

    def fetch_pair(
        self,
        heroes: list[dict],
        backend: str = "api",
        overwrite: bool = False,
        use_rag: bool = True,
    ) -> bool:
        """指定获取：按用户选择跳过或覆盖已有相性。

        返回是否成功启动子进程；忙碌等未启动场景不发完成信号，调用方据此避免无限等待。
        """
        return self._submit("--synergy-pair", heroes, mode="pair",
                            backend=backend, overwrite=overwrite, use_rag=use_rag)

    def fetch_single(
        self,
        hero: dict,
        all_heroes: list[dict],
        backend: str = "api",
        use_rag: bool = True,
    ) -> bool:
        """选定武将：传入 1 个武将，写入临时文件后调用 --synergy-single"""
        return self._submit("--synergy-single", [hero], mode="single",
                            backend=backend, use_rag=use_rag)

    def fetch_pairs_list(
        self,
        pairs: list[dict],
        backend: str = "api",
        overwrite: bool = False,
        use_rag: bool = True,
    ) -> bool:
        """实战配队清单：传入显式 id 配对列表，写入临时文件后调用 --synergy-list

        pairs 元素格式：{"hero_a_id": int, "hero_b_id": int}
        """
        return self._submit("--synergy-list", pairs, mode="pairs_list",
                            backend=backend, overwrite=overwrite, use_rag=use_rag)

    def _submit(
        self,
        args_flag: str,
        payload: list[dict],
        *,
        mode: str,
        backend: str,
        use_rag: bool = True,
        overwrite: bool = False,
    ) -> bool:
        """payload 写临时文件 → 拼 CLI 参数 → 启动子进程（三段公共流程）。"""
        if self._is_busy():
            return False
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name
        tmp.close()
        self._context = {
            "mode": mode,
            "tmp_path": tmp_path,
            "backend": backend,
            "overwrite": overwrite,
            "use_rag": use_rag,
        }
        self.status_changed.emit("正在生成相性评分...")
        args = ["-m", "src.scraper.ai_batch", args_flag, tmp_path]
        if not use_rag:
            args.append("--no-rag")
        if overwrite:
            args.append("--update")
        if backend == "browser":
            args.append("--browser")
        self._start_process(args)
        return True

    # ---------------------------------------------------------------
    # 钩子
    # ---------------------------------------------------------------

    def _on_stdout_line(self, line: str) -> None:
        """解析子进程进度行。"""
        if not line:
            return

        if is_generation_progress_line(line):
            self.progress_output.emit(line)
        # 只有生成结果完成校验（OK / FAIL）或确认跳过后才推进进度。
        m = re.search(r"\[(\d+)/(\d+)\].*\s(?:OK|FAIL|SKIP)(?:\s|$|（)", line)
        if m:
            self.progress_value.emit(int(m.group(1)), int(m.group(2)))

    def _on_process_finished(self, exit_code: int) -> None:
        """仅以 CLI 的结构化退出码判断生成成败。"""
        if exit_code == 0:
            self.fetch_completed.emit(True, "相性生成完成")


