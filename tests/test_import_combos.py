"""名将杀 Agent - 实战配队导入脚本单元测试"""

import json
import tempfile
from pathlib import Path

import pytest
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


def test_deleted_records_block_source_and_survive(tmp_path: Path) -> None:
    """逻辑删除永久屏蔽同 key 源记录（内容更新也不复活），源中消失仍保留。"""
    output = tmp_path / "combos.json"
    _seed_output(output, [
        {"hero1_name": "刘备", "hero2_name": "孙权", "hero1_id": 1, "hero2_id": 2,
         "rating": 8, "position": "both", "note": "旧导出记录", "manual": False,
         "deleted": True},
        {"hero1_name": "刘备", "hero2_name": "吕布", "hero1_id": 1, "hero2_id": 3,
         "rating": 5, "position": "both", "note": "旧导出记录", "manual": False},
    ])
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"combos": [
        {"hero1": "孙权", "hero2": "刘备", "rating": 9, "position": "both", "note": "源更新版"},
    ]}, ensure_ascii=False), encoding="utf-8")
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES)

    report = run_import(source, heroes, output)

    mgr = ComboManager(output)
    mgr.load()
    deleted = mgr.get_combo(1, 2)
    assert deleted.deleted is True and deleted.rating == 8  # 源更新未进入
    assert len(mgr.list_combos()) == 0  # 展示查询过滤已删除
    assert [f"{i['hero1']}+{i['hero2']}" for i in report["deleted_skipped"]] == ["孙权+刘备"]
    assert report["imported"] == 0
    assert [f"{i['hero1']}+{i['hero2']}" for i in report["removed_stale"]] == ["刘备+吕布"]
    assert mgr.get_combo(1, 3) is None  # 活跃旧记录仍按 removed_stale 移除


def test_idempotent_rerun_with_deleted(tmp_path: Path) -> None:
    """含已删除记录时重复导入输出仍稳定。"""
    output = tmp_path / "combos.json"
    _seed_output(output, [
        {"hero1_name": "刘备", "hero2_name": "孙权", "hero1_id": 1, "hero2_id": 2,
         "rating": 8, "position": "both", "note": "旧导出记录", "manual": False,
         "deleted": True, "deleted_at": "2026-09-01T10:00:00"},
    ])
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"combos": [
        {"hero1": "刘备", "hero2": "孙权", "rating": 9, "position": "both", "note": "源记录"},
        {"hero1": "吕布", "hero2": "张辽", "rating": 6, "position": "both", "note": "12 34"},
    ]}, ensure_ascii=False), encoding="utf-8")
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES)

    run_import(source, heroes, output)
    first = output.read_text(encoding="utf-8")
    run_import(source, heroes, output)
    assert output.read_text(encoding="utf-8") == first


def test_restored_imported_record_upserts_when_in_source(tmp_path: Path) -> None:
    """恢复后的记录回到正常生命周期：源中重新出现则正常 upsert。"""
    output = tmp_path / "combos.json"
    _seed_output(output, [
        {"hero1_name": "刘备", "hero2_name": "孙权", "hero1_id": 1, "hero2_id": 2,
         "rating": 8, "position": "both", "note": "旧记录", "manual": False,
         "deleted": True},
    ])
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES)

    # 界面恢复（清除删除标记）后再导入
    restore_mgr = ComboManager(output)
    restore_mgr.load()
    restore_mgr.restore_combo(restore_mgr.get_combo(1, 2))

    source = tmp_path / "source.json"
    source.write_text(json.dumps({"combos": [
        {"hero1": "刘备", "hero2": "孙权", "rating": 9, "position": "both", "note": "源记录"},
    ]}, ensure_ascii=False), encoding="utf-8")
    report = run_import(source, heroes, output)

    mgr = ComboManager(output)
    mgr.load()
    combo = mgr.get_combo(1, 2)
    assert combo.deleted is False and combo.rating == 9
    assert report["deleted_skipped"] == []


def test_hero_name_alias_mapping(tmp_path: Path) -> None:
    """hero1/hero2 字段的手录简称（临海/平阳）映射到全名武将，落库存全名。"""
    output = tmp_path / "combos.json"
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"combos": [
        {"hero1": "临海", "hero2": "刘备", "rating": 7, "position": "both", "note": "0"},
        {"hero1": "孙权", "hero2": "平阳", "rating": 8, "position": "both", "note": "0"},
    ]}, ensure_ascii=False), encoding="utf-8")
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES + [{"id": 5, "name": "临海公主"}, {"id": 6, "name": "平阳公主"}])

    report = run_import(source, heroes, output)

    assert report["unmatched"] == []
    assert report["imported"] == 2
    mgr = ComboManager(output)
    mgr.load()
    assert mgr.get_combo(1, 5).hero1_name == "临海公主"
    assert mgr.get_combo(2, 6).hero2_name == "平阳公主"


@pytest.mark.parametrize("bad_source", ['"一段文本"', "123", "null", "true"])
def test_rejects_scalar_source_with_path_in_error(tmp_path: Path, bad_source: str) -> None:
    """源 JSON 顶层非对象/数组（误选纯值 JSON）时抛带路径的 ValueError，不写输出。"""
    source = tmp_path / "source.json"
    source.write_text(bad_source, encoding="utf-8")
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES)
    output = tmp_path / "combos.json"

    with pytest.raises(ValueError, match="source.json"):
        run_import(source, heroes, output)
    assert not output.exists()


def test_rejects_non_list_combos_field(tmp_path: Path) -> None:
    """combos 字段非数组（如字符串）时抛带路径的 ValueError，不写输出。"""
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"combos": "abc"}), encoding="utf-8")
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES)
    output = tmp_path / "combos.json"

    with pytest.raises(ValueError, match="source.json"):
        run_import(source, heroes, output)
    assert not output.exists()


def test_import_snapshots_existing_output_before_overwrite(tmp_path: Path) -> None:
    """覆盖已有 combos.json 前先快照到同目录 backups/（误选源文件可回退）。"""
    output = tmp_path / "combos.json"
    _seed_output(output, [
        {"hero1_name": "刘备", "hero2_name": "孙权", "hero1_id": 1, "hero2_id": 2,
         "rating": 8, "position": "both", "note": "旧记录"},
    ])
    before = output.read_text(encoding="utf-8")
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"combos": [
        {"hero1": "吕布", "hero2": "张辽", "rating": 6, "position": "both", "note": "12 34"},
    ]}, ensure_ascii=False), encoding="utf-8")
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES)

    run_import(source, heroes, output)

    backups = list((tmp_path / "backups").glob("combos-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == before


def test_first_import_creates_no_backup(tmp_path: Path) -> None:
    """输出文件不存在（首次导入）时不产生备份。"""
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"combos": []}, ensure_ascii=False), encoding="utf-8")
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES)
    output = tmp_path / "combos.json"

    run_import(source, heroes, output)

    assert output.exists()
    assert not (tmp_path / "backups").exists()


# ---------------------------------------------------------------
# 回归：审查修复批（VUL-B4-002 / VUL-B4-003）
# ---------------------------------------------------------------

@pytest.mark.parametrize("bad_heroes", [
    {"data": HEROES},          # 顶层对象缺 heroes 数组（同步工具改格式的典型形态）
    {"heroes": {"1": "刘备"}},  # heroes 字段非数组
    [],                        # 空列表 → 空映射
])
def test_rejects_bad_heroes_map_without_touching_output(tmp_path: Path, bad_heroes) -> None:
    """回归（VUL-B4-002）：武将映射无法识别/为空时中止导入，既有 combos.json 字节不变。

    空映射放行会让全部源记录落 unmatched、既有导入记录全判 stale 而被清空，
    且界面仍显示"导入完成"。
    """
    output = tmp_path / "combos.json"
    _seed_output(output, [
        {"hero1_name": "刘备", "hero2_name": "孙权", "hero1_id": 1, "hero2_id": 2,
         "rating": 8, "position": "both", "note": "旧记录"},
    ])
    before = output.read_text(encoding="utf-8")
    source = tmp_path / "source.json"
    _write_json(source, {"combos": SOURCE_COMBOS})
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, bad_heroes)

    with pytest.raises(ValueError, match="heroes.json"):
        run_import(source, heroes, output)

    assert output.read_text(encoding="utf-8") == before


def test_invalid_source_record_keeps_existing_combo(tmp_path: Path) -> None:
    """回归（VUL-B4-003）：源记录字段校验失败时保留同 key 既有记录，不判为 stale 移除。"""
    output = tmp_path / "combos.json"
    _seed_output(output, [
        {"hero1_name": "刘备", "hero2_name": "孙权", "hero1_id": 1, "hero2_id": 2,
         "rating": 8, "position": "both", "note": "旧记录"},
    ])
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"combos": [
        {"hero1": "刘备", "hero2": "孙权", "rating": 99, "position": "both",
         "note": "0", "video_url": "", "updated": "2026-08-19"},
        {"hero1": "吕布", "hero2": "张辽", "rating": 6, "position": "both", "note": "12 34"},
    ]}, ensure_ascii=False), encoding="utf-8")
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES)

    report = run_import(source, heroes, output)

    assert len(report["invalid"]) == 1
    assert report["removed_stale"] == []  # 同 key 既有记录未被移除
    mgr = ComboManager(output)
    mgr.load()
    assert mgr.get_combo(1, 2).rating == 8
    assert mgr.get_combo(3, 4).rating == 6  # 正常源记录照常导入

    # 幂等：源中坏记录仍在时重复导入输出稳定
    run_import(source, heroes, output)
    first = output.read_text(encoding="utf-8")
    run_import(source, heroes, output)
    assert output.read_text(encoding="utf-8") == first


# ---------------------------------------------------------------
# 回归：审查修复批（VUL-B1-002）
# ---------------------------------------------------------------

def test_rejects_output_pointing_at_other_data_file(tmp_path: Path) -> None:
    """回归（VUL-B1-002）：--output 误指其他数据文件时中止导入，目标文件字节不变。

    武将形列表逐条 Combo 校验全败、内存降级空集，旧逻辑照常导入会把
    目标文件原内容整体覆盖（如 heroes.json 被写成配队数据/空数组）。
    """
    output = tmp_path / "output.json"
    _write_json(output, HEROES)
    before = output.read_text(encoding="utf-8")
    source = tmp_path / "source.json"
    _write_json(source, {"combos": SOURCE_COMBOS})
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES)

    with pytest.raises(ValueError, match="已中止导入"):
        run_import(source, heroes, output)

    assert output.read_text(encoding="utf-8") == before


def test_rejects_dict_shaped_output(tmp_path: Path) -> None:
    """回归（VUL-B1-002）：输出文件顶层为对象（invalid_root）时中止导入。"""
    output = tmp_path / "output.json"
    _write_json(output, {"combos": SOURCE_COMBOS})
    before = output.read_text(encoding="utf-8")
    source = tmp_path / "source.json"
    _write_json(source, {"combos": SOURCE_COMBOS})
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES)

    with pytest.raises(ValueError, match="output.json"):
        run_import(source, heroes, output)

    assert output.read_text(encoding="utf-8") == before


def test_rejects_output_with_corrupt_records(tmp_path: Path) -> None:
    """输出文件混有坏记录时同样拒绝（行为收口）：静默丢弃坏记录再覆盖保存，
    会让"读不出来的记录"经一次看似成功的导入被静默固化清除。"""
    output = tmp_path / "combos.json"
    records = [
        {"hero1_name": "刘备", "hero2_name": "孙权", "hero1_id": 1, "hero2_id": 2,
         "rating": 8, "position": "both", "note": "旧记录"},
        {"hero1_name": "残缺记录"},
    ]
    output.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    source = tmp_path / "source.json"
    _write_json(source, {"combos": SOURCE_COMBOS})
    heroes = tmp_path / "heroes.json"
    _write_json(heroes, HEROES)

    with pytest.raises(ValueError, match="1 条错误"):
        run_import(source, heroes, output)

    assert json.loads(output.read_text(encoding="utf-8")) == records
