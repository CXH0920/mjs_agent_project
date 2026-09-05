"""名将杀 Agent - DataFacade 数据完整性测试"""

import json
import tempfile
import threading
import time
from pathlib import Path

from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.manager import DataFacade
from src.data.models import Hero, HeroGuide, SynergyScore
from src.data.synergy_manager import SynergyManager


def _write_json(path: Path, data: list[dict]) -> str:
    source = json.dumps(data, ensure_ascii=False)
    path.write_text(source, encoding="utf-8")
    return source


def test_load_all_reports_missing_references_without_mutating_loaded_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        heroes_path = tmp_path / "heroes.json"
        synergies_path = tmp_path / "synergies.json"
        guides_path = tmp_path / "guides.json"

        _write_json(
            heroes_path,
            [
                Hero(id=1, name="曹操").model_dump(mode="json"),
                Hero(id=2, name="刘备").model_dump(mode="json"),
            ],
        )
        synergies_source = _write_json(
            synergies_path,
            [
                SynergyScore(hero_a_id=1, hero_b_id=2, score=6).model_dump(mode="json"),
                SynergyScore(hero_a_id=1, hero_b_id=99, score=6).model_dump(mode="json"),
            ],
        )
        guides_source = _write_json(
            guides_path,
            [
                HeroGuide(hero_id=1, weak_against_type=["高爆发型"], synergizes_with=[99]).model_dump(mode="json"),
                HeroGuide(hero_id=88, weak_against_type=["慢速防御型"]).model_dump(mode="json"),
            ],
        )

        facade = DataFacade(heroes_path, synergies_path, guides_path)
        report = facade.load_all()

        assert [score.hero_b_id for score in facade.synergies.list_synergies()] == [2, 99]
        guide = facade.guides.get_guide(1)
        assert guide.weak_against_type == ["高爆发型"]
        assert guide.synergizes_with == [99]
        assert facade.guides.get_guide(88) is not None
        assert report is facade.last_load_report
        assert report.error_count == 3
        assert all(issue.kind == "missing_reference" for issue in report.issues)
        assert synergies_path.read_text(encoding="utf-8") == synergies_source
        assert guides_path.read_text(encoding="utf-8") == guides_source


def test_from_managers_initializes_complete_facade() -> None:
    facade = DataFacade.from_managers(HeroManager(), SynergyManager(), GuideManager())

    assert isinstance(facade.last_load_report.issues, list)
    assert facade.heroes.__class__ is HeroManager


def test_data_manager_save_uses_lf_newlines() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "heroes.json"
        manager = HeroManager(path)
        manager.add_hero(Hero(id=1, name="曹操"))

        manager.save()

        assert b"\r\n" not in path.read_bytes()
        assert path.read_bytes().endswith(b"\n")


def test_data_manager_thread_safe_concurrent_read_write() -> None:
    """并发读写同一 DataManager 不崩溃、不读到半更新状态。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = HeroManager(Path(tmpdir) / "heroes.json")
        for i in range(1, 11):
            manager.add_hero(Hero(id=i, name=f"武将{i}"))

        stop = threading.Event()
        errors: list[BaseException] = []

        def writer() -> None:
            try:
                next_id = 1000
                while not stop.is_set():
                    manager.add_hero(Hero(id=next_id, name=f"写{next_id}"))
                    manager.update_hero(Hero(id=next_id, name=f"改{next_id}"))
                    manager.delete_hero(next_id)
                    next_id += 1
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        def reader() -> None:
            try:
                while not stop.is_set():
                    names = [hero.name for hero in manager.list_heroes()]
                    assert len(names) == len(set(names)), "读到重复名称，说明快照不一致"
                    assert manager.get(1) is not None
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]
        for thread in threads:
            thread.start()
        time.sleep(0.2)
        stop.set()
        for thread in threads:
            thread.join(timeout=5)

        assert errors == []
        assert manager.get(1) is not None
        assert len(manager.list_heroes()) == 10
