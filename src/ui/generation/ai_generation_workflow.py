"""攻略和相性生成的 UI 工作流。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget
from src.business.ai_cost import estimate_generation_cost
from src.business.fetching.guide_fetch_service import GuideFetchService
from src.business.fetching.synergy_fetch_service import SynergyFetchService
from src.data.combo_manager import ComboManager
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.synergy_manager import SynergyManager
from src.ui.app.chinese_translator import install_details_button_translator
from src.ui.generation.backend_choose_dialog import BackendChooseDialog
from src.ui.generation.guide_fetch_dialog import GuideFetchDialog
from src.ui.generation.guide_progress_dialog import GuideProgressDialog
from src.ui.generation.synergy_combos_dialog import SynergyCombosDialog
from src.ui.generation.synergy_pair_dialog import SynergyPairDialog
from src.ui.generation.synergy_single_dialog import SynergySingleDialog


class AiGenerationWorkflow(QObject):
    """编排 AI 任务所需的选择、确认、进度展示和完成处理。"""

    status_changed = Signal(str)
    guides_changed = Signal()
    synergies_changed = Signal()

    def __init__(
        self,
        hero_manager: HeroManager,
        guide_manager: GuideManager,
        synergy_manager: SynergyManager,
        guide_service: GuideFetchService,
        synergy_service: SynergyFetchService,
        parent: QWidget | None = None,
        combo_manager: ComboManager | None = None,
    ) -> None:
        super().__init__(parent)
        self._hero_manager = hero_manager
        self._guide_manager = guide_manager
        self._synergy_manager = synergy_manager
        self._guide_service = guide_service
        self._synergy_service = synergy_service
        self._combo_manager = combo_manager or ComboManager()
        self._window = parent
        self._guide_progress_dialog: GuideProgressDialog | None = None
        self._synergy_progress_dialog: GuideProgressDialog | None = None
        self._connect_services()

    def set_window(self, window) -> None:
        """更新弹窗/对话框归属的主窗口引用（组合根挂载阶段调用）。"""
        self._window = window

    def _connect_services(self) -> None:
        self._guide_service.status_changed.connect(self.status_changed)
        self._guide_service.fetch_completed.connect(self._on_guide_completed)
        self._guide_service.cancelled.connect(self._on_guide_cancelled)
        self._guide_service.error_occurred.connect(self._on_guide_error)
        self._guide_service.progress_output.connect(self._on_guide_progress)
        self._guide_service.progress_value.connect(self._on_guide_progress_value)

        self._synergy_service.status_changed.connect(self.status_changed)
        self._synergy_service.fetch_completed.connect(self._on_synergy_completed)
        self._synergy_service.cancelled.connect(self._on_synergy_cancelled)
        self._synergy_service.error_occurred.connect(self._on_synergy_error)
        self._synergy_service.progress_output.connect(self._on_synergy_progress)
        self._synergy_service.progress_value.connect(self._on_synergy_progress_value)
        self._synergy_service.reload_finished.connect(self._on_synergy_reload_finished)
        self._synergy_service.reload_failed.connect(self._on_synergy_reload_failed)

    def request_guide_all(self) -> None:
        heroes = self._require_heroes()
        if not heroes:
            return
        self._start_guide_generation(heroes, "all", "全量攻略生成", self._guide_service.fetch_all)

    def request_guide_incremental(self) -> None:
        heroes = self._require_heroes()
        if not heroes:
            return
        existing_ids = {guide.hero_id for guide in self._guide_manager.list_guides()}
        missing = [hero for hero in heroes if hero.get("id") not in existing_ids]
        if not missing:
            self.status_changed.emit("所有武将已有攻略，无需生成")
            return
        self._start_guide_generation(
            missing,
            "incremental",
            "增量攻略生成",
            self._guide_service.fetch_incremental,
        )

    def request_guide_specific(self) -> None:
        dialog = GuideFetchDialog(
            self._hero_manager,
            self._guide_manager,
            parent=self._window,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_heroes:
            return
        self._start_guide_generation(
            dialog.selected_heroes,
            "specific",
            "指定攻略生成",
            self._guide_service.fetch_specific,
        )

    def request_synergy_pair(self) -> None:
        if not self._require_heroes():
            return
        dialog = SynergyPairDialog(
            self._hero_manager,
            self._synergy_manager,
            parent=self._window,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_heroes:
            return

        selected = dialog.selected_heroes
        pair_count = len(selected) * (len(selected) - 1) // 2
        estimation = estimate_generation_cost(pair_count, "synergy")
        estimation["estimate_kind"] = "synergy"
        choice = self._choose_backend("相性配对生成", estimation)
        if choice is None:
            return
        backend, use_rag = choice
        self._start_synergy_generation(
            pair_count,
            "相性配对生成进度",
            lambda: self._synergy_service.fetch_pair(
                selected,
                backend=backend,
                overwrite=dialog.overwrite_existing,
                use_rag=use_rag,
            ),
        )

    def request_synergy_single(self) -> None:
        all_heroes = self._require_heroes()
        if not all_heroes:
            return
        dialog = SynergySingleDialog(self._hero_manager, parent=self._window)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_hero:
            return

        pair_count = len(all_heroes) - 1
        estimation = estimate_generation_cost(pair_count, "synergy")
        estimation["estimate_kind"] = "synergy"
        choice = self._choose_backend("选定武将相性生成", estimation)
        if choice is None:
            return
        backend, use_rag = choice
        self._start_synergy_generation(
            pair_count,
            "选定武将相性生成进度",
            lambda: self._synergy_service.fetch_single(
                dialog.selected_hero,
                all_heroes,
                backend=backend,
                use_rag=use_rag,
            ),
        )

    def request_synergy_combos(self) -> None:
        """实战配队批量生成：按评级/座次/生成状态筛选 combos 配对清单。"""
        if not self._require_heroes():
            return
        dialog = SynergyCombosDialog(
            self._synergy_manager,
            combo_manager=self._combo_manager,
            parent=self._window,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_pairs:
            return

        pairs = dialog.selected_pairs
        estimation = estimate_generation_cost(len(pairs), "synergy")
        estimation["estimate_kind"] = "synergy"
        choice = self._choose_backend("实战配队相性生成", estimation)
        if choice is None:
            return
        backend, use_rag = choice
        self._start_synergy_generation(
            len(pairs),
            "实战配队相性生成进度",
            lambda: self._synergy_service.fetch_pairs_list(
                pairs,
                backend=backend,
                overwrite=dialog.overwrite_existing,
                use_rag=use_rag,
            ),
        )

    def _require_heroes(self) -> list[dict]:
        heroes = self._get_heroes_as_dicts()
        if not heroes:
            QMessageBox.warning(self._window, "提示", "没有武将数据，请先采集武将")
        return heroes

    def _start_guide_generation(
        self,
        heroes: list[dict],
        mode: str,
        title: str,
        fetch: Callable[[list[dict], str, bool], bool],
    ) -> None:
        if self._guide_service.is_busy:
            QMessageBox.warning(self._window, "生成进行中", "已有攻略生成任务在运行，请等待完成。")
            return
        estimation = estimate_generation_cost(len(heroes), "guide")
        estimation["mode"] = mode
        estimation["heroes"] = heroes
        estimation["estimate_kind"] = "guide"
        choice = self._choose_backend(title, estimation)
        if choice is None:
            return
        backend, use_rag = choice

        # 未启动子进程（忙碌/无需生成）不会有完成信号，必须确认启动成功后再进
        # 模态 exec，否则进度框将永久卡死只能杀进程
        if not fetch(heroes, backend, use_rag):
            return
        self._guide_progress_dialog = GuideProgressDialog(len(heroes), parent=self._window)
        self._guide_progress_dialog.cancel_requested.connect(self._guide_service.cancel)
        self._guide_progress_dialog.exec()
        self._guide_progress_dialog = None

    def _start_synergy_generation(
        self,
        item_count: int,
        title: str,
        start: Callable[[], bool],
    ) -> None:
        if self._synergy_service.is_busy:
            QMessageBox.warning(self._window, "生成进行中", "已有相性生成任务在运行，请等待完成。")
            return
        if not start():
            # 同攻略流程：未启动子进程不会有完成信号，不能进入模态 exec
            return
        self._synergy_progress_dialog = GuideProgressDialog(
            item_count,
            title=title,
            item_label="相性评分",
            parent=self._window,
        )
        self._synergy_progress_dialog.cancel_requested.connect(self._synergy_service.cancel)
        self._synergy_progress_dialog.exec()
        self._synergy_progress_dialog = None

    def _choose_backend(self, title: str, estimation: dict | None = None) -> tuple[str, bool] | None:
        """返回 (backend, use_rag)；用户取消时返回 None。"""
        dialog = BackendChooseDialog(estimation=estimation, title=title, parent=self._window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.get_selected_backend(), dialog.get_selected_rag()

    def _on_guide_completed(self, success: bool, message: str = "") -> None:
        if self._guide_progress_dialog:
            self._guide_progress_dialog.on_process_finished(success, message)
        if success:
            self._guide_manager.load()
            self.guides_changed.emit()
        else:
            QMessageBox.warning(self._window, "生成失败", f"攻略生成失败\n{message}")

    def _on_guide_error(self, error_message: str) -> None:
        if self._guide_progress_dialog:
            self._guide_progress_dialog.on_process_finished(False, error_message)
        failed = self._guide_service.failed_items
        detail = (
            f"失败武将（{len(failed)}）：\n" + "\n".join(failed)
            if failed else error_message
        )
        message = QMessageBox(self._window)
        message.setIcon(QMessageBox.Icon.Critical)
        message.setWindowTitle("攻略生成失败")
        message.setText("攻略生成出错")
        message.setDetailedText(detail)
        install_details_button_translator(message)
        message.exec()

    def _on_guide_cancelled(self) -> None:
        if self._guide_progress_dialog:
            self._guide_progress_dialog.on_process_cancelled()
        self._guide_manager.load()
        self.guides_changed.emit()

    def _on_guide_progress(self, text: str) -> None:
        if self._guide_progress_dialog:
            self._guide_progress_dialog.update_status(text)

    def _on_guide_progress_value(self, current: int, total: int) -> None:
        if self._guide_progress_dialog:
            self._guide_progress_dialog.update_progress(current, total)

    def _on_synergy_completed(self, success: bool, message: str = "") -> None:
        if self._synergy_progress_dialog:
            self._synergy_progress_dialog.on_process_finished(success, message)
        if success:
            self._synergy_manager.load()
            self.synergies_changed.emit()
        else:
            QMessageBox.warning(self._window, "生成失败", f"相性评分生成失败\n{message}")

    def _on_synergy_error(self, error_message: str) -> None:
        if self._synergy_progress_dialog:
            self._synergy_progress_dialog.on_process_finished(False, error_message)
        failed = self._synergy_service.failed_items
        detail = (
            f"失败项（{len(failed)}）：\n" + "\n".join(failed)
            if failed else error_message
        )
        message = QMessageBox(self._window)
        message.setIcon(QMessageBox.Icon.Critical)
        message.setWindowTitle("相性生成失败")
        message.setText("相性评分生成出错")
        message.setDetailedText(detail)
        install_details_button_translator(message)
        message.exec()

    def _on_synergy_cancelled(self) -> None:
        if self._synergy_progress_dialog:
            self._synergy_progress_dialog.on_process_cancelled()
        # 取消后后台重载已分批提交的相性数据，保持主界面可响应（worker 与写回都在服务内）
        self._synergy_service.reload_from_disk()

    def _on_synergy_reload_finished(self) -> None:
        self.synergies_changed.emit()

    def _on_synergy_reload_failed(self, message: str) -> None:
        self.status_changed.emit(f"相性数据重载失败: {message}")

    def _on_synergy_progress(self, text: str) -> None:
        if self._synergy_progress_dialog:
            self._synergy_progress_dialog.update_status(text)

    def _on_synergy_progress_value(self, current: int, total: int) -> None:
        if self._synergy_progress_dialog:
            self._synergy_progress_dialog.update_progress(current, total)

    def _get_heroes_as_dicts(self) -> list[dict]:
        return [
            {
                "id": hero.id,
                "name": hero.name,
                "faction": hero.faction,
                "max_hp": hero.max_hp,
                "max_hand": hero.max_hand,
                "position": hero.position,
                "gender": hero.gender,
                "difficulty": hero.difficulty,
                "title": hero.title,
                "skills": [
                    {"name": skill.name, "description": skill.description,
                     "settlement": skill.settlement}
                    for skill in (hero.skills or [])
                ],
            }
            for hero in self._hero_manager.list_heroes()
        ]
