# -*- coding: utf-8 -*-
"""combo_import_service 同名测试：源清单/武将名表加载的纯函数与防御（D5 零覆盖冒烟补测）。"""

from __future__ import annotations

import json

import pytest
from src.business.maintenance.combo_import_service import (
    _load_hero_name_map,
    _load_source_combos,
)


def test_load_hero_name_map_accepts_array_and_object_forms(tmp_path) -> None:
    array_form = tmp_path / "heroes_a.json"
    array_form.write_text(json.dumps([{"id": 1, "name": "甲"}, {"id": 2, "name": "乙"}]),
                          encoding="utf-8")
    object_form = tmp_path / "heroes_o.json"
    object_form.write_text(json.dumps({"heroes": [{"id": 3, "name": "丙"}]}),
                           encoding="utf-8")

    assert _load_hero_name_map(array_form) == {"甲": 1, "乙": 2}
    assert _load_hero_name_map(object_form) == {"丙": 3}


def test_load_hero_name_map_rejects_empty_mapping_with_path(tmp_path) -> None:
    """空映射必须中止（放行会把既有导入记录全判 stale 清空），报错带文件路径。"""
    path = tmp_path / "heroes.json"
    path.write_text(json.dumps([]), encoding="utf-8")

    with pytest.raises(ValueError, match="映射为空"):
        _load_hero_name_map(path)


def test_load_source_combos_variants(tmp_path) -> None:
    list_form = tmp_path / "s1.json"
    list_form.write_text(json.dumps([{"hero_a": "甲", "hero_b": "乙"}]), encoding="utf-8")
    object_form = tmp_path / "s2.json"
    object_form.write_text(json.dumps({"combos": [{"hero_a": "甲"}]}), encoding="utf-8")

    assert _load_source_combos(list_form) == [{"hero_a": "甲", "hero_b": "乙"}]
    assert _load_source_combos(object_form) == [{"hero_a": "甲"}]

    bad = tmp_path / "s3.json"
    bad.write_text(json.dumps({"combos": "not-a-list"}), encoding="utf-8")
    with pytest.raises(ValueError, match="必须是数组"):
        _load_source_combos(bad)
