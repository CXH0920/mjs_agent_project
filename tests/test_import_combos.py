"""名将杀 Agent - 实战配队导入脚本单元测试"""

import json
import tempfile
from pathlib import Path

from src.data.combo_manager import ComboManager
from src.scripts.import_combos import run_import

HEROES = [
    {"id": 1, "name": "刘备"},
    {"id": 2, "name": "孙权"},
    {"id": 3, "name": "吕布"},
    {"id": 4, "name": "张辽"},
]

# 与真实导出一致的 combos 结构样例
SOURCE_COMBOS = [
    {"hero1": "刘备", "hero2": "孙权", "rating": 9, "position": "14",
     "note": "孙权4+刘备1：刘备留一张牌发动孙权技能", "video_url": "", "updated": "2026-08-19"},
    {"hero1": "张辽", "hero2": "吕布", "rating": 8, "position": "both",
     "note": "牢布1张辽4：先手压製", "video_url": "", "updated": "2026-08-19"},
    {"hero1": "刘备", "hero2": "吕布", "rating": 6, "position": "both",
     "note": "12 34", "video_url": "", "updated": "2026-08-19"},
    {"hero1": "刘备", "hero2": "不存在武将", "rating": 5, "position": "both",
     "note": "1 2", "video_url": "", "updated": "2026-08-19"},
    {"hero1": "孙权", "hero2": "刘备", "rating": 7, "position": "both",
     "note": "重复配对应被跳过", "video_url": "", "updated": "2026-08-19"},
    {"hero1": "吕布", "hero2": "张辽", "rating": 7, "position": "23",
     "note": "重复配对（反向）应被跳过", "video_url": "", "updated": "2026-08-19"},
]


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _import(tmpdir: Path):
    source = tmpdir / "source.json"
    heroes = tmpdir / "heroes.json"
    output = tmpdir / "combos.json"
    _write_json(source, {"version": 1, "combos": SOURCE_COMBOS})
    _write_json(heroes, HEROES)
    report = run_import(source, heroes, output)
    return report, output


class TestRunImport:
    """run_import 导入流程"""

    def test_import_counts_and_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report, output = _import(Path(tmpdir))

            assert report["total"] == 6
            assert report["imported"] == 3  # 6 - 未匹配1 - 重复2
            assert report["unmatched"] == [
                {"index": 3, "hero1": "刘备", "hero2": "不存在武将"}
            ]
            assert len(report["duplicates"]) == 2

            mgr = ComboManager(output)
            mgr.load()
            assert len(mgr.list_combos()) == 3

            combo = mgr.get_combo(1, 2)  # 刘备+孙权
            assert combo.rating == 9
            assert combo.hero1_name == "刘备"
            assert combo.hero1_seats == [1]
            assert combo.hero2_seats == [4]
            assert combo.position == "14"

    def test_alias_and_bare_token_seats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _, output = _import(Path(tmpdir))
            mgr = ComboManager(output)
            mgr.load()

            alias_combo = mgr.get_combo(3, 4)  # 张辽+吕布，note 用"牢布"
            assert alias_combo.hero1_seats == [4]
            assert alias_combo.hero2_seats == [1]

            bare_combo = mgr.get_combo(1, 3)  # 刘备+吕布，note "12 34"
            assert bare_combo.hero1_seats == [1, 2]
            assert bare_combo.hero2_seats == [3, 4]

    def test_position_mismatch_reported(self):
        """note 座次与 position 字段矛盾时进报告，position 留原值"""
        source = [dict(c) for c in SOURCE_COMBOS[:1]]
        source[0]["position"] = "23"  # note 为 1/4 号位，position 却是 23
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            src = tmp / "source.json"
            heroes = tmp / "heroes.json"
            output = tmp / "combos.json"
            _write_json(src, {"combos": source})
            _write_json(heroes, HEROES)
            report = run_import(src, heroes, output)

            assert len(report["position_mismatch"]) == 1
            assert report["position_mismatch"][0]["position"] == "23"
            mgr = ComboManager(output)
            mgr.load()
            assert mgr.get_combo(1, 2).position == "23"  # 原值保留

    def test_seat_review_for_unparsed(self):
        """部分解析/失败的条目照常导入（座次留空）并进复核清单"""
        source = [
            {"hero1": "吕布", "hero2": "张辽", "rating": 7, "position": "both",
             "note": "吕布5号位非法数字", "video_url": "", "updated": "2026-08-19"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            src = tmp / "source.json"
            heroes = tmp / "heroes.json"
            output = tmp / "combos.json"
            _write_json(src, {"combos": source})
            _write_json(heroes, HEROES)
            report = run_import(src, heroes, output)

            assert report["imported"] == 1
            assert report["seat_stats"]["parsed"] == 0
            assert len(report["seat_review"]) == 1
            mgr = ComboManager(output)
            mgr.load()
            combo = mgr.get_combo(3, 4)
            assert combo.hero1_seats == [] and combo.hero2_seats == []

    def test_idempotent_rerun(self):
        """重复执行输出稳定（幂等）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _, output = _import(Path(tmpdir))
            first = output.read_text(encoding="utf-8")
            _import(Path(tmpdir))
            second = output.read_text(encoding="utf-8")
            assert first == second

    def test_invalid_rating_reported(self):
        source = [
            {"hero1": "刘备", "hero2": "孙权", "rating": 99, "position": "both",
             "note": "0", "video_url": "", "updated": "2026-08-19"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            src = tmp / "source.json"
            heroes = tmp / "heroes.json"
            output = tmp / "combos.json"
            _write_json(src, {"combos": source})
            _write_json(heroes, HEROES)
            report = run_import(src, heroes, output)

            assert report["imported"] == 0
            assert len(report["invalid"]) == 1


def _seed_output(output: Path, combos: list[dict]) -> None:
    from src.data.models import Combo

    mgr = ComboManager(output)
    for c in combos:
        combo = Combo(**c)
        mgr.update(combo, tuple(sorted((combo.hero1_id, combo.hero2_id))))
    mgr.save()


def _source_only(comb: list[dict]) -> Path:
    return Path(json.dumps({"combos": comb}, ensure_ascii=False))


def test_manual_records_survive_import(tmp_path: Path) -> None:
    """manual 手工记录不在源导出中时，导入后原样保留。"""
    output = tmp_path / "combos.json"
    _seed_output(output, [
        {"hero1_name": "刘备", "hero2_name": "孙权", "hero1_id": 1, "hero2_id": 2,
         "rating": 8, "position": "both", "note": "手工记录", "manual": True},
        {"hero1_name": "刘备", "hero2_name": "吕布", "hero1_id": 1, "hero2_id": 3,
         "rating": 5, "position": "both", "note": "旧导出记录", "manual": False},
    ])
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"combos": [
        {"hero1": "吕布", "hero2": "张辽", "rating": 6, "position": "both", "note": "12 34"},
    ]}, ensure_ascii=False), encoding="utf-8")
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES)

    report = run_import(source, heroes, output)

    mgr = ComboManager(output)
    mgr.load()
    assert len(mgr.list_combos()) == 2
    kept = mgr.get_combo(1, 2)
    assert kept.manual is True and kept.note == "手工记录" and kept.rating == 8
    assert mgr.get_combo(1, 3) is None  # 非手工且源中不存在 → 移除
    assert [f"{i['hero1']}+{i['hero2']}" for i in report["removed_stale"]] == ["刘备+吕布"]
    assert len(report["manual_kept"]) == 1
    assert report["imported"] == 1


def test_manual_wins_on_collision(tmp_path: Path) -> None:
    """源导出与手工记录同 key 冲突时，保留手工版本并进报告。"""
    output = tmp_path / "combos.json"
    _seed_output(output, [
        {"hero1_name": "刘备", "hero2_name": "孙权", "hero1_id": 1, "hero2_id": 2,
         "rating": 7, "position": "both", "note": "手工修正版", "manual": True},
    ])
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"combos": [
        {"hero1": "刘备", "hero2": "孙权", "rating": 9, "position": "14", "note": "孙权4+刘备1"},
    ]}, ensure_ascii=False), encoding="utf-8")
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES)

    report = run_import(source, heroes, output)

    mgr = ComboManager(output)
    mgr.load()
    combo = mgr.get_combo(1, 2)
    assert combo.manual is True and combo.rating == 7 and combo.note == "手工修正版"
    assert len(report["manual_collisions"]) == 1
    assert report["imported"] == 0
    assert report["removed_stale"] == []


def test_idempotent_rerun_with_manual(tmp_path: Path) -> None:
    """含手工记录时重复导入输出仍稳定。"""
    output = tmp_path / "combos.json"
    _seed_output(output, [
        {"hero1_name": "刘备", "hero2_name": "孙权", "hero1_id": 1, "hero2_id": 2,
         "rating": 8, "position": "both", "note": "手工记录", "manual": True},
    ])
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"combos": [
        {"hero1": "吕布", "hero2": "张辽", "rating": 6, "position": "both", "note": "12 34"},
    ]}, ensure_ascii=False), encoding="utf-8")
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES)

    run_import(source, heroes, output)
    first = output.read_text(encoding="utf-8")
    run_import(source, heroes, output)
    assert output.read_text(encoding="utf-8") == first
