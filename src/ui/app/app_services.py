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

import logging
import time

from PySide6.QtCore import QCoreApplication
from src.business.announcement.announcement_service import AnnouncementService
from src.business.card_sync import CardSyncService
from src.business.emulator.capture_service import CaptureService
from src.business.fetching.guide_fetch_service import GuideFetchService
from src.business.fetching.hero_fetch_service import HeroFetchService
from src.business.fetching.synergy_fetch_service import SynergyFetchService
from src.business.recognition.ocr_service import OcrService
from src.config.env import get_mumu_config
from src.data.announcement_manager import AnnouncementManager
from src.data.card_catalog import CardRepository
from src.data.card_points_repository import CardPointsRepository
from src.data.facade import DataFacade
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.manager import (
    DEFAULT_GUIDES_FILE,
    DEFAULT_HEROES_FILE,
    DEFAULT_SYNERGIES_FILE,
)
from src.data.peak_win_rate_repository import load_peak_pick_ranks, load_peak_win_rates
from src.data.synergy_manager import SynergyManager
from src.data.win_rate_repository import load_win_rates
from src.ui.app.poll_coordinator import PollCoordinator
from src.ui.generation.ai_generation_workflow import AiGenerationWorkflow

logger = logging.getLogger(__name__)


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
        self.capture.update_config(get_mumu_config())
        self.ocr.update_config(get_mumu_config())
        self.ocr.set_hero_names([h.name for h in self.data.heroes.list_heroes()])
        self.poll = PollCoordinator(
            self.capture,
            self.ocr,
            lambda: [hero.name for hero in self.data.heroes.list_heroes()],
        )

        self.announcement_manager = AnnouncementManager()
        # 启动即加载：漏加载会让磁盘上的已处理状态不可见，已 applied 的
        # 公告每次检查都被当新公告重新入库、反复提示待更新
        self.announcement_manager.load()
        self.announcement_service = AnnouncementService(
            self.announcement_manager, self.data.heroes,
        )

        # 卡牌官网同步（独立于公告体系；仓库启动即加载，供检查基线与同步写回使用）
        self.card_repository = CardRepository()
        self.card_repository.load()
        self.card_sync_service = CardSyncService(self.card_repository)
        # 卡牌点数表仓库：打开卡牌同步对话框时惰性加载，供候选摘要的点数提醒
        self.card_points_repository = CardPointsRepository()

        # 官方榜单读取 provider：榜 CSV 的加载入口由组合根统一持有，
        # UI（巅峰选将/对局攻略）经构造注入消费，不再自带 data 层 import
        self.win_rates_provider = load_win_rates
        self.peak_win_rates_provider = load_peak_win_rates
        self.peak_pick_ranks_provider = load_peak_pick_ranks

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
            self.card_sync_service,
        ):
            qobject.setParent(parent)
        # AiGenerationWorkflow 缓存窗口引用作弹窗归属（组合根无头构造时为 None）
        self.ai_workflow.set_window(parent)

    def shutdown_fetch_services(self, timeout_ms: int = 2000) -> None:
        """中止三个抓取子进程，并最多等待 timeout_ms 让进程树真正退出。

        QProcess 随父对象析构不杀子进程树：browser 模式下 Playwright 的
        Edge/node 会孤儿化并锁住 data/edge_profile，下次浏览器模式启动报
        locked（VUL-B2-002）。先发终止请求，再用事件循环泵等待，避免直接
        sleep 卡住 GUI；超时后不再等待，保证退出不被阻塞。
        """
        services = (self.hero_fetch, self.guide_fetch, self.synergy_fetch)
        for service in services:
            try:
                service.cancel()
            except RuntimeError:
                pass  # 对象已随窗口销毁，退出路径无需再处理
        if not any(service.is_busy for service in services):
            return
        app = QCoreApplication.instance()
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if not any(service.is_busy for service in services):
                return
            if app is not None:
                app.processEvents()
            time.sleep(0.02)
        logger.warning("退出时抓取子进程未在 %d ms 内结束，不再等待", timeout_ms)
