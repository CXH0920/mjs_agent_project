"""数据管理服务的备份和批量清空测试。"""

from __future__ import annotations

import json

import pytest

from src.business.data_management_service import DataManagementService, DataMutationService
from src.data.hero_manager import HeroManager
from src.data.guide_manager import GuideManager
from src.data.models import Hero, HeroGuide, SynergyScore
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


def test_clear_data_restores_all_files_when_one_save_fails(tmp_path, monkeypatch) -> None:
    guide_manager, synergy_manager = _managers(tmp_path)
    guide_source = guide_manager.file_path.read_text(encoding="utf-8")
    synergy_source = synergy_manager.file_path.read_text(encoding="utf-8")

    def fail_save() -> None:
        raise OSError("磁盘写入失败")

    monkeypatch.setattr(synergy_manager, "save", fail_save)

    with pytest.raises(OSError, match="磁盘写入失败"):
        DataManagementService(guide_manager, synergy_manager).clear_data(
            guides=True, synergies=True,
        )

    assert guide_manager.list_guides()[0].description == "旧攻略"
    assert len(synergy_manager.list_synergies()) == 1
    assert guide_manager.file_path.read_text(encoding="utf-8") == guide_source
    assert synergy_manager.file_path.read_text(encoding="utf-8") == synergy_source


def test_repair_missing_references_requires_explicit_service_call(tmp_path) -> None:
    heroes = HeroManager(tmp_path / "heroes.json")
    guides = GuideManager(tmp_path / "guides.json")
    synergies = SynergyManager(tmp_path / "synergies.json")
    heroes.add_hero(Hero(id=1, name="曹操"))
    guides.add_guide(HeroGuide(hero_id=1, synergizes_with=[99]))
    guides.add_guide(HeroGuide(hero_id=88, description="失效攻略"))
    synergies.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=99, score=6))
    heroes.save()
    guides.save()
    synergies.save()

    result = DataMutationService(heroes, guides, synergies).repair_missing_references()

    assert (result.removed_synergies, result.removed_guides, result.cleaned_guide_references) == (1, 1, 1)
    assert guides.get_guide(1).synergizes_with == []
    assert guides.get_guide(88) is None
    assert synergies.list_synergies() == []
    assert len(result.backup_paths) == 2


def test_delete_hero_with_relations_commits_all_related_files(tmp_path) -> None:
    heroes = HeroManager(tmp_path / "heroes.json")
    guides = GuideManager(tmp_path / "guides.json")
    synergies = SynergyManager(tmp_path / "synergies.json")
    heroes.add_hero(Hero(id=1, name="曹操"))
    guides.add_guide(HeroGuide(hero_id=1, description="旧攻略"))
    synergies.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=2, score=6))
    heroes.save()
    guides.save()
    synergies.save()

    backups = DataMutationService(heroes, guides, synergies).delete_hero_with_relations(1)

    assert heroes.get_hero(1) is None
    assert guides.get_guide(1) is None
    assert synergies.list_synergies() == []
    assert len(backups) == 3
