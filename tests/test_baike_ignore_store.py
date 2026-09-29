"""百科忽略名单（baike_ignore_store）单元测试：持久化降级与过滤矩阵。"""

from __future__ import annotations

from src.data.baike_ignore_store import (
    BaikeIgnoreStore,
    IgnoreEntry,
    filter_ignored,
    ignore_entry,
    load_baike_ignores,
    remove_entry,
)


def _entry(state: str = "modified", hash_: str = "h1") -> IgnoreEntry:
    return IgnoreEntry(name="关羽", state=state, hash=hash_, ignored_at="2026-09-29T00:00:00")


def test_load_missing_file_returns_empty_store(tmp_path) -> None:
    store = load_baike_ignores(tmp_path / "none.json")
    assert store == BaikeIgnoreStore()


def test_load_corrupted_file_degrades_to_empty(tmp_path) -> None:
    path = tmp_path / "ignore.json"
    path.write_text("{broken json", encoding="utf-8")

    assert load_baike_ignores(path) == BaikeIgnoreStore()


def test_ignore_and_remove_entry_roundtrip(tmp_path) -> None:
    path = tmp_path / "ignore.json"
    ignore_entry("cards", "5", "火杀", "modified", "abc", path)
    ignore_entry("heroes", "3", "关羽", "added", "def", path)

    store = load_baike_ignores(path)
    assert store.cards["5"].name == "火杀"
    assert store.heroes["3"].state == "added"

    remove_entry("cards", "5", path)
    store = load_baike_ignores(path)
    assert "5" not in store.cards
    assert "3" in store.heroes
    remove_entry("cards", "5", path)  # 幂等：删除不存在的条目不抛错


def test_filter_ignored_matrix() -> None:
    entries = {
        "1": _entry("modified", "h1"),   # hash 匹配 → 过滤
        "2": _entry("modified", "old"),  # hash 不匹配（官网已变）→ 保留
        "3": _entry("removed"),          # removed 按 state 匹配 → 过滤
        "4": _entry("modified", "h4"),   # 现在是 added，state 不匹配 → 保留
    }
    diff = {
        "added": [{"id": 4, "name": "丁"}],
        "modified": [{"id": 1, "name": "甲"}, {"id": 2, "name": "乙"}, {"id": 9, "name": "壬"}],
        "removed": [{"id": 3, "name": "丙"}],
    }
    official_hashes = {"1": "h1", "2": "h2", "4": "h4"}

    filtered, count = filter_ignored(diff, official_hashes, entries)

    assert filtered == {
        "added": [{"id": 4, "name": "丁"}],
        "modified": [{"id": 2, "name": "乙"}, {"id": 9, "name": "壬"}],
        "removed": [],
    }
    assert count == 2


def test_filter_ignored_empty_entries_keeps_all() -> None:
    diff = {"added": [{"id": 1, "name": "甲"}], "modified": [], "removed": []}

    filtered, count = filter_ignored(diff, {"1": "h1"}, {})

    assert filtered == diff
    assert count == 0
