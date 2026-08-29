"""攻略和相性生成的 UI 工作流。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from src.business.fetching.guide_fetch_service import GuideFetchService
from src.business.fetching.synergy_fetch_service import SynergyFetchService
from src.business.fetching.synergy_reload_worker import SynergyReloadWorker
from src.data.combo_manager import ComboManager
from src.data.manager import DataIssue
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import SynergyScore
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
        self._synergy_reload_worker: SynergyReloadWorker | None = None
        self._connect_services()

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
        from src.config.env import get_api_config
        from src.scraper.ai.prompt_utils import estimate_item_cost

        estimation = estimate_item_cost(pair_count, "synergy", get_api_config()["model"])
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
        from src.config.env import get_api_config
        from src.scraper.ai.prompt_utils import estimate_item_cost

        estimation = estimate_item_cost(pair_count, "synergy", get_api_config()["model"])
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
        from src.config.env import get_api_config
        from src.scraper.ai.prompt_utils import estimate_item_cost

        estimation = estimate_item_cost(len(pairs), "synergy", get_api_config()["model"])
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
        fetch: Callable[[list[dict], str, bool], None],
    ) -> None:
        from src.config.env import get_api_config
        from src.scraper.ai.prompt_utils import estimate_cost

        estimation = estimate_cost(len(heroes), "guide", get_api_config()["model"])
        estimation["mode"] = mode
        estimation["heroes"] = heroes
        estimation["estimate_kind"] = "guide"
        choice = self._choose_backend(title, estimation)
        if choice is None:
            return
        backend, use_rag = choice

        self._guide_progress_dialog = GuideProgressDialog(len(heroes), parent=self._window)
        self._guide_progress_dialog.cancel_requested.connect(self._guide_service.cancel)
        fetch(heroes, backend, use_rag)
        self._guide_progress_dialog.exec()
        self._guide_progress_dialog = None

    def _start_synergy_generation(
        self,
        item_count: int,
        title: str,
        start: Callable[[], None],
    ) -> None:
        self._synergy_progress_dialog = GuideProgressDialog(
            item_count,
            title=title,
            item_label="相性评分",
            parent=self._window,
        )
        self._synergy_progress_dialog.cancel_requested.connect(self._synergy_service.cancel)
        start()
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
        self._reload_synergies_in_background()

    def _reload_synergies_in_background(self) -> None:
        """取消后后台重载已分批提交的相性数据，保持主界面可响应。"""
        if self._synergy_reload_worker and self._synergy_reload_worker.isRunning():
            return
        worker = SynergyReloadWorker(self._synergy_manager.file_path, self)
        worker.loaded.connect(self._on_synergy_reload_completed)
        worker.failed.connect(self._on_synergy_reload_failed)
        worker.finished.connect(worker.deleteLater)
        self._synergy_reload_worker = worker
        worker.start()

    def _on_synergy_reload_completed(
        self,
        synergies: list[SynergyScore],
        load_issues: list[DataIssue],
    ) -> None:
        self._synergy_manager.replace_loaded_data(synergies, load_issues)
        self._synergy_reload_worker = None
        self.synergies_changed.emit()

    def _on_synergy_reload_failed(self, message: str) -> None:
        self._synergy_reload_worker = None
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
