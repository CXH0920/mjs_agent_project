"""攻略生成服务测试。"""

from __future__ import annotations

from src.business.guide_fetch_service import GuideFetchService
from src.data.guide_manager import GuideManager
from src.data.models import HeroGuide


def test_incremental_fetch_without_missing_heroes_reports_status(tmp_path) -> None:
    guide_manager = GuideManager(tmp_path / "guides.json")
    guide_manager.add_guide(HeroGuide(hero_id=1))
    service = GuideFetchService(guide_manager)
    statuses: list[str] = []
    service.status_changed.connect(statuses.append)

    service.fetch_incremental([{"id": 1, "name": "曹操"}])

    assert statuses == ["所有武将已有攻略，无需生成"]
