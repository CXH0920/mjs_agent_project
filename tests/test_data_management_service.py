"""数据管理服务的备份和批量清空测试。"""

from __future__ import annotations

import json

import pytest

from src.business.data_management_service import DataManagementService
from src.data.guide_manager import GuideManager
from src.data.models import HeroGuide, SynergyScore
from src.data.synergy_manager import SynergyManager


def _managers(tmp_path):
    guide_manager = GuideManager(tmp_path / "guides.json")
    synergy_manager = SynergyManager(tmp_path / "synergies.json")
    guide_manager.add_guide(HeroGuide(hero_id=1, description="旧攻略"))
    synergy_manager.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=2, score=6))
    guide_manager.save()
    synergy_manager.save()
    return guide_manager, synergy_manager


def test_clear_selected_data_creates_backup_and_preserves_unselected_data(tmp_path) -> None:
    guide_manager, synergy_manager = _managers(tmp_path)
    service = DataManagementService(guide_manager, synergy_manager)

    result = service.clear_data(guides=True, synergies=False)

    assert result.cleared_guides == 1
    assert result.cleared_synergies == 0
    assert guide_manager.list_guides() == []
    assert len(synergy_manager.list_synergies()) == 1
    assert json.loads(guide_manager.file_path.read_text(encoding="utf-8")) == []
    assert len(result.backup_paths) == 1
    assert json.loads(result.backup_paths[0].read_text(encoding="utf-8"))[0]["description"] == "旧攻略"


def test_clear_both_data_types_creates_two_backups(tmp_path) -> None:
    guide_manager, synergy_manager = _managers(tmp_path)

    result = DataManagementService(guide_manager, synergy_manager).clear_data(
        guides=True, synergies=True,
    )

    assert (result.cleared_guides, result.cleared_synergies) == (1, 1)
    assert json.loads(guide_manager.file_path.read_text(encoding="utf-8")) == []
    assert json.loads(synergy_manager.file_path.read_text(encoding="utf-8")) == []
    assert len(result.backup_paths) == 2


def test_clear_data_requires_selection(tmp_path) -> None:
    guide_manager, synergy_manager = _managers(tmp_path)

    with pytest.raises(ValueError, match="至少选择"):
        DataManagementService(guide_manager, synergy_manager).clear_data(
            guides=False, synergies=False,
        )
