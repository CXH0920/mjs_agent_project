# -*- coding: utf-8 -*-
"""character_feature_repository 同名测试：缓存读写、分层合并与查询降级（D5 锚点迁移，自 test_ocr_components 归位）。"""

from __future__ import annotations

import json
import logging
import sys
from types import ModuleType

import pytest
from src.ocr.character_feature_repository import CharacterFeatureRepository
from src.scripts.build_character_feature_cache import COMMON_OCR_CONFUSION_CHARACTERS, HEROES_PATH, required_characters


def test_character_feature_repository_saves_injected_cache_path_with_utf8_lf(tmp_path) -> None:
    cache_path = tmp_path / "char_info_cache.json"
    cache_path.write_text(
        json.dumps({"曹": {"pinyin": "cao"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    repository = CharacterFeatureRepository(cache_path)

    entries = repository.load()
    entries["操"] = {"pinyin": "cao", "total_strokes": "16"}
    repository.save()

    content = cache_path.read_bytes()
    assert not content.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in content
    assert json.loads(content.decode("utf-8"))["操"]["total_strokes"] == "16"


def test_character_feature_repository_reads_unihan_csv_from_options_destination(tmp_path, monkeypatch) -> None:
    csv_path = tmp_path / "export" / "unihan.csv"
    csv_path.parent.mkdir()
    csv_path.write_text(
        "char,kCangjie,kFourCornerCode\n曹,HV,5500.0\n",
        encoding="utf-8",
    )

    class FakeOptions:
        def __init__(self, **_kwargs) -> None:
            self.destination = csv_path

    class FakePackager:
        def __init__(self, _options) -> None:
            pass

        def export(self) -> None:
            raise AssertionError("已有 UNIHAN CSV 时不应重复导出")

    module = ModuleType("unihan_etl.core")
    module.Options = FakeOptions
    module.Packager = FakePackager
    monkeypatch.setitem(sys.modules, "unihan_etl.core", module)
    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")

    assert repository._query_unihan("曹") == {"cangjie": "HV", "four_corner": "5500"}


def test_character_feature_repository_logs_pinyin_warmup_failure(tmp_path, monkeypatch, caplog) -> None:
    module = ModuleType("pypinyin")
    module.Style = type("Style", (), {"NORMAL": "normal"})

    def fail_pinyin(*_args, **_kwargs):
        raise RuntimeError("pinyin unavailable")

    module.pinyin = fail_pinyin
    monkeypatch.setitem(sys.modules, "pypinyin", module)
    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")
    caplog.set_level(logging.WARNING)

    repository.warmup()

    assert repository._pinyin_available is False
    assert "pypinyin 预热失败" in caplog.text


def test_character_feature_repository_warms_missing_wordlist_characters(tmp_path, monkeypatch) -> None:
    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")
    repository.load()["曹"] = {"pinyin": "cao"}
    built: list[str] = []
    monkeypatch.setattr(
        repository,
        "_build_feature",
        lambda char: built.append(char) or {"pinyin": char},
    )

    assert repository.warmup_characters(["曹", "操", "操"]) == 1
    assert built == ["操"]
    assert repository.get_feature("操") == {"pinyin": "操"}


def test_character_feature_repository_merges_user_layer_over_baseline(tmp_path) -> None:
    baseline = tmp_path / "baseline.json"
    user = tmp_path / "user.json"
    baseline.write_text(
        json.dumps({"曹": {"pinyin": "cao"}, "操": {"pinyin": "cao"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    user.write_text(json.dumps({"操": {"pinyin": "cao2"}}, ensure_ascii=False), encoding="utf-8")

    repository = CharacterFeatureRepository(baseline, user_cache_path=user)
    entries = repository.load()

    assert entries["曹"]["pinyin"] == "cao"
    assert entries["操"]["pinyin"] == "cao2"


def test_warmup_characters_persists_new_chars_to_user_layer(tmp_path, monkeypatch) -> None:
    baseline = tmp_path / "baseline.json"
    user = tmp_path / "user.json"
    baseline.write_text(
        json.dumps({"曹": {"pinyin": "cao"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    repository = CharacterFeatureRepository(baseline, user_cache_path=user)
    built: list[str] = []
    monkeypatch.setattr(
        repository,
        "_build_feature",
        lambda char: built.append(char) or {"pinyin": char},
    )

    assert repository.warmup_characters(["曹", "操", "操"]) == 1
    assert built == ["操"]
    assert user.exists()

    second = CharacterFeatureRepository(baseline, user_cache_path=user)
    monkeypatch.setattr(
        second,
        "_build_feature",
        lambda _char: (_ for _ in ()).throw(AssertionError("用户层命中后不应重建")),
    )
    assert second.get_feature("操") == {"pinyin": "操"}


def test_character_feature_repository_tolerates_user_layer_write_failure(
    tmp_path, monkeypatch, caplog
) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}", encoding="utf-8")
    (tmp_path / "blocked").write_text("x", encoding="utf-8")
    user = tmp_path / "blocked" / "user.json"

    repository = CharacterFeatureRepository(baseline, user_cache_path=user)
    monkeypatch.setattr(repository, "_build_feature", lambda char: {"pinyin": char})
    caplog.set_level(logging.WARNING)

    assert repository.warmup_characters(["甲"]) == 1
    assert repository.get_feature("甲") == {"pinyin": "甲"}
    assert "用户层缓存写入失败" in caplog.text


def test_character_feature_cache_character_set_includes_names_and_common_misreads(tmp_path) -> None:
    heroes_path = tmp_path / "heroes.json"
    heroes_path.write_text(
        json.dumps([{"name": "乐毅"}, {"name": "嬴政"}], ensure_ascii=False),
        encoding="utf-8",
    )

    assert required_characters(heroes_path) == set("乐毅嬴政") | set(COMMON_OCR_CONFUSION_CHARACTERS)


def test_static_character_feature_cache_covers_current_hero_names_and_common_misreads() -> None:
    if not HEROES_PATH.exists():
        pytest.skip("真实武将数据已出库：python -m src.scripts.pull_data pull 后运行")
    entries = CharacterFeatureRepository().load()

    assert required_characters() <= entries.keys()
    for char in COMMON_OCR_CONFUSION_CHARACTERS:
        assert entries[char].get("four_corner")
        assert entries[char].get("cangjie")
        assert entries[char].get("radical")
        assert entries[char].get("total_strokes")


def test_character_feature_repository_logs_pinyin_query_failure_once(tmp_path, monkeypatch, caplog) -> None:
    module = ModuleType("pypinyin")
    module.Style = type("Style", (), {"NORMAL": "normal"})

    def fail_pinyin(*_args, **_kwargs):
        raise RuntimeError("pinyin unavailable")

    module.pinyin = fail_pinyin
    monkeypatch.setitem(sys.modules, "pypinyin", module)
    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")
    caplog.set_level(logging.WARNING)

    assert repository._get_pinyin("曹") == ""
    assert repository._get_pinyin("操") == ""

    assert repository._pinyin_available is False
    assert caplog.text.count("pypinyin 查询失败") == 1


def test_character_feature_repository_logs_radical_query_failure(tmp_path, monkeypatch, caplog) -> None:
    class BrokenRadical:
        def trans_ch(self, _char: str) -> str:
            raise RuntimeError("radical unavailable")

    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")
    repository._radical_client = BrokenRadical()
    monkeypatch.setattr(repository, "_query_unihan", lambda _char: {})
    monkeypatch.setattr(repository, "_get_pinyin", lambda _char: "")
    monkeypatch.setattr(repository, "_get_stroke", lambda _char: 0)
    caplog.set_level(logging.WARNING)

    feature = repository._build_feature("曹")

    assert feature["radical"] == ""
    assert "cnradical 查询失败" in caplog.text
    assert "曹" in caplog.text
