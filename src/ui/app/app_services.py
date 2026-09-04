# -*- coding: utf-8 -*-
"""MainWindow 协作对象组合根：装配顺序与参数依赖收敛一处，可无头构造（F1）。

MainWindow 持有本对象后调用 attach(parent) 统一挂载，再解包到惯用属性；
后续接线/建 UI 代码与拆分前零差异。
注意：
- AiGenerationWorkflow 额外缓存窗口引用作弹窗归属（_window），attach 时经
  set_window 特例回填，不能只 setParent；
- ComboManager/AnnouncementManager 继承 DataManager，非 QObject，仅持引用。
"""
from __future__ import annotations

from src.business.announcement.announcement_service import AnnouncementService
from src.business.emulator.capture_service import CaptureService
from src.business.fetching.guide_fetch_service import GuideFetchService
from src.business.fetching.hero_fetch_service import HeroFetchService
from src.business.fetching.synergy_fetch_service import SynergyFetchService
from src.business.recognition.ocr_service import OcrService
from src.config.env import get_mumu_config
from src.data.announcement_manager import AnnouncementManager
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.manager import (
    DEFAULT_GUIDES_FILE,
    DEFAULT_HEROES_FILE,
    DEFAULT_SYNERGIES_FILE,
    DataFacade,
)
from src.data.synergy_manager import SynergyManager
from src.ui.app.poll_coordinator import PollCoordinator
from src.ui.generation.ai_generation_workflow import AiGenerationWorkflow


class AppServices:
    """MainWindow 的协作对象组合根。"""

    def __init__(
        self,
        hero_manager=None,
        synergy_manager=None,
        guide_manager=None,
    ):
        if hero_manager or synergy_manager or guide_manager:
            self.data = DataFacade.from_managers(
                hero_manager or HeroManager(heroes_file=DEFAULT_HEROES_FILE),
                synergy_manager or SynergyManager(synergies_file=DEFAULT_SYNERGIES_FILE),
                guide_manager or GuideManager(guides_file=DEFAULT_GUIDES_FILE),
            )
        else:
            self.data = DataFacade(
                heroes_file=DEFAULT_HEROES_FILE,
                synergies_file=DEFAULT_SYNERGIES_FILE,
                guides_file=DEFAULT_GUIDES_FILE,
            )

        self.hero_fetch = HeroFetchService()
        self.guide_fetch = GuideFetchService(self.data.guides)
        self.synergy_fetch = SynergyFetchService(self.data.synergies)
        from src.data.combo_manager import ComboManager
        self.combo_manager = ComboManager()
        self.ai_workflow = AiGenerationWorkflow(
            self.data.heroes,
            self.data.guides,
            self.data.synergies,
            self.guide_fetch,
            self.synergy_fetch,
            combo_manager=self.combo_manager,
        )

        # 屏幕采集服务
        self.capture = CaptureService()
        self.ocr = OcrService()
        self.ocr.set_ocr_task_submitter(self.capture.submit_ocr_task)
        self.capture.update_config(get_mumu_config())
        self.ocr.update_config(get_mumu_config())
        self.ocr.set_hero_names([h.name for h in self.data.heroes.list_heroes()])
        self.poll = PollCoordinator(
            self.capture,
            self.ocr,
            lambda: [hero.name for hero in self.data.heroes.list_heroes()],
        )

        self.announcement_manager = AnnouncementManager()
        self.announcement_service = AnnouncementService(
            self.announcement_manager, self.data.heroes,
        )

    def attach(self, parent) -> None:
        """MainWindow 持有本对象后统一挂载 QObject 父子与窗口引用。"""
        for qobject in (
            self.hero_fetch,
            self.guide_fetch,
            self.synergy_fetch,
            self.ai_workflow,
            self.capture,
            self.ocr,
            self.poll,
            self.announcement_service,
        ):
            qobject.setParent(parent)
        # AiGenerationWorkflow 缓存窗口引用作弹窗归属（组合根无头构造时为 None）
        self.ai_workflow.set_window(parent)
