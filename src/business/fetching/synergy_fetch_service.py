"""
名将杀 Agent - 相性获取业务服务

负责编排 AI 相性评分生成流程，管理 QProcess 生命周期。
支持选定武将（单武将 x 全体）和指定获取（两个武将配对）两种模式。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

from PySide6.QtCore import Signal
from src.business.fetching.base_fetch_service import BaseFetchService
from src.business.fetching.fetch_utils import is_generation_progress_line, parse_generation_event
from src.business.fetching.synergy_reload_worker import SynergyReloadWorker

logger = logging.getLogger(__name__)


class SynergyFetchService(BaseFetchService):
    """相性获取业务服务"""

    progress_output = Signal(str)        # 原始 stdout 行
    progress_value = Signal(int, int)    # (current, total) 供进度条使用
    fetch_completed = Signal(bool, str)
    reload_finished = Signal()
    reload_failed = Signal(str)

    def __init__(self, synergy_manager, parent=None):
        super().__init__(parent)
        self._synergy_manager = synergy_manager
        self._reload_worker = None
        self._reload_mtime: float | None = None

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

    def reload_from_disk(self) -> bool:
        """后台重载相性文件并写回数据层（取消生成后保住已分批提交的数据）。

        返回是否成功启动；抓取子进程运行中或已有重载进行中时不重复启动，
        不发完成信号（抓取中重载会用启动前的旧快照覆盖内存态）。
        """
        if self._is_busy():
            return False
        try:
            self._reload_mtime = os.path.getmtime(self._synergy_manager.file_path)
        except OSError:
            self._reload_mtime = None
        worker = SynergyReloadWorker(self._synergy_manager.file_path, self)
        worker.loaded.connect(self._on_reload_loaded)
        worker.failed.connect(self._on_reload_failed)
        worker.finished.connect(worker.deleteLater)
        self._reload_worker = worker
        worker.start()
        return True

    @property
    def reloading(self) -> bool:
        """后台重载是否进行中（供 UI 区分拦截文案，不改变忙碌语义）。"""
        return self._is_reload_running()

    def _is_reload_running(self) -> bool:
        """重载线程是否仍在运行；worker 已被 deleteLater 销毁时视为未运行。"""
        worker = self._reload_worker
        if worker is None:
            return False
        try:
            return worker.isRunning()
        except RuntimeError:
            # C++ 对象已销毁：引用失效，按未运行处理并清空，避免下次 isRunning 再抛
            self._reload_worker = None
            return False

    def _is_busy(self) -> bool:
        """抓取子进程与后台重载共用同一把互斥。

        只拦「抓取中重载」是不够的：重载线程读的是启动前的旧快照，期间若
        启动生成子进程并分批提交，重载完成会用旧快照覆盖新数据（VUL-B3-002）。
        """
        if self._is_reload_running():
            logger.warning("相性数据重载进行中，忽略请求")
            return True
        return super()._is_busy()

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

    def _on_reload_loaded(self, synergies, load_issues) -> None:
        """重载完成后把结果原子写回共享 manager，再广播完成信号。

        写回前比对文件 mtime：worker 读的是启动时快照，若期间子进程又提交了
        新批次，直接写回会把新数据覆盖成旧快照。此时放弃写回，让调用方按
        需要重新触发一次重载。
        """
        if self._reload_stale():
            logger.warning("相性文件在重载期间已变化，放弃写回旧快照")
            self._reload_worker = None
            self.reload_finished.emit()
            return
        self._synergy_manager.replace_loaded_data(synergies, load_issues)
        self._reload_worker = None
        self.reload_finished.emit()

    def _reload_stale(self) -> bool:
        """重载期间相性文件是否被改动过。"""
        try:
            mtime = os.path.getmtime(self._synergy_manager.file_path)
        except OSError:
            return False
        return mtime != self._reload_mtime

    def _on_reload_failed(self, error_msg: str) -> None:
        """重载失败同样要释放 worker 引用（与 _on_reload_loaded 对称）。

        worker 经 finished→deleteLater 销毁 C++ 对象后，残留引用会让
        下次 reload_from_disk 的 isRunning() 抛 RuntimeError。
        """
        self._reload_worker = None
        self.reload_failed.emit(error_msg)

    def _on_stdout_line(self, line: str) -> None:
        """解析子进程进度行。"""
        if not line:
            return

        if is_generation_progress_line(line):
            self.progress_output.emit(line)
        # 只有生成结果完成校验（OK / FAIL）或确认跳过后才推进进度。
        event = parse_generation_event(line)
        if event is not None and event.kind in ("ok", "fail", "skip"):
            self.progress_value.emit(event.current, event.total)

    def _on_process_finished(self, exit_code: int) -> None:
        """仅以 CLI 的结构化退出码判断生成成败。"""
        if exit_code == 0:
            self.fetch_completed.emit(True, "相性生成完成")


