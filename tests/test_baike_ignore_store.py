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


def test_ignore_entry_corrupt_file_backed_up_before_overwrite(tmp_path) -> None:
    """损坏的忽略名单被覆盖写之前须按原字节留底（用户压制记录不可重建）。"""
    path = tmp_path / "ignore.json"
    corrupt = "{broken json"
    path.write_text(corrupt, encoding="utf-8")

    ignore_entry("cards", "5", "火杀", "modified", "abc", path)

    backups = list((tmp_path / "backups").glob("ignore-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == corrupt
    # 降级后文件仅含新条目（既有行为保持）
    assert "5" in load_baike_ignores(path).cards


def test_filter_ignored_empty_entries_keeps_all() -> None:
    diff = {"added": [{"id": 1, "name": "甲"}], "modified": [], "removed": []}

    filtered, count = filter_ignored(diff, {"1": "h1"}, {})

    assert filtered == diff
    assert count == 0


def test_filter_ignored_matches_by_name_when_id_key_absent() -> None:
    """UI 忽略本地未收录的新增武将时以武将名作键（VUL-B4-102 回归）。

    这类条目没有本地 id，diff 过滤若只认 id 就永不命中，而候选侧
    _hero_is_ignored 有按名兜底，两处判定分叉会表现为「横幅提示有变化、
    点更新却没有候选」。
    """
    entries = {"东方朔": IgnoreEntry(name="东方朔", state="added", hash="h188")}
    diff = {"added": [{"id": 188, "name": "东方朔"}], "modified": [], "removed": []}

    filtered, count = filter_ignored(diff, {"188": "h188"}, entries)

    assert filtered == {"added": [], "modified": [], "removed": []}
    assert count == 1


def test_filter_ignored_name_fallback_still_requires_hash_match() -> None:
    """按名兜底同样受哈希门控：官网内容再变即重现，不被永久压制。"""
    entries = {"东方朔": IgnoreEntry(name="东方朔", state="added", hash="old")}
    diff = {"added": [{"id": 188, "name": "东方朔"}], "modified": [], "removed": []}

    filtered, count = filter_ignored(diff, {"188": "new"}, entries)

    assert filtered == diff
    assert count == 0


def test_filter_ignored_id_match_wins_over_name_fallback() -> None:
    """id 命中优先：名单里同时存在 id 键与同名条目时，按 id 条目判定。

    官网哈希按 diff 条目的真实 id 取，因此 id 键条目即使 hash 不匹配也应
    走「不压制」分支，而不会被同名条目顶替成命中。
    """
    entries = {
        "188": IgnoreEntry(name="东方朔", state="added", hash="stale"),
        "旧名": IgnoreEntry(name="东方朔", state="added", hash="h188"),
    }
    diff = {"added": [{"id": 188, "name": "东方朔"}], "modified": [], "removed": []}

    filtered, count = filter_ignored(diff, {"188": "h188"}, entries)

    # 命中 id 键（hash=stale ≠ h188）→ 不压制，不回落到同名条目
    assert filtered == diff
    assert count == 0
