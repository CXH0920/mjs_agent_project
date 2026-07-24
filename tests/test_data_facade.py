"""名将杀 Agent - DataFacade 数据完整性测试"""

import json
import tempfile
from pathlib import Path

from src.data.manager import DataFacade
from src.data.models import Hero, HeroGuide, SynergyScore


def _write_json(path: Path, data: list[dict]) -> str:
    source = json.dumps(data, ensure_ascii=False)
    path.write_text(source, encoding="utf-8")
    return source


def test_load_all_reports_and_recovers_missing_references():
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

        assert [score.hero_b_id for score in facade.synergies.list_synergies()] == [2]
        guide = facade.guides.get_guide(1)
        assert guide.weak_against_type == ["高爆发型"]
        assert guide.synergizes_with == []
        assert facade.guides.get_guide(88) is None
        assert report is facade.last_load_report
        assert report.error_count == 3
        assert all(issue.kind == "missing_reference" for issue in report.issues)
        assert synergies_path.read_text(encoding="utf-8") == synergies_source
        assert guides_path.read_text(encoding="utf-8") == guides_source
