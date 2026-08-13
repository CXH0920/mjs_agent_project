"""Git 风格 diff 纯函数测试。"""

from __future__ import annotations

from src.ui.shared.rich_diff import (
    KIND_DELETE,
    KIND_EQUAL,
    KIND_INSERT,
    build_diff_rows,
    rows_to_html,
)


def _marks(rows: list[dict]) -> list[str]:
    return [row["mark"] for row in rows]


def test_no_change_all_normal() -> None:
    rows = build_diff_rows("第一行\n第二行", "第一行\n第二行")
    assert _marks(rows) == [" ", " "]
    assert all(seg[1] == KIND_EQUAL for row in rows for seg in row["segments"])


def test_insert_line() -> None:
    rows = build_diff_rows("第一行", "第一行\n新增行")
    assert _marks(rows) == [" ", "+"]
    added = rows[1]
    assert added["whole"] is True
    assert added["segments"] == [("新增行", KIND_INSERT)]


def test_delete_line() -> None:
    rows = build_diff_rows("第一行\n删除行", "第一行")
    assert _marks(rows) == [" ", "-"]
    deleted = rows[1]
    assert deleted["whole"] is True
    assert deleted["segments"] == [("删除行", KIND_DELETE)]


def test_replace_block_with_char_level_segments() -> None:
    rows = build_diff_rows("old line", "new line")
    assert _marks(rows) == ["-", "+"]
    delete_row, insert_row = rows
    assert delete_row["whole"] is False
    assert insert_row["whole"] is False
    # 相同后缀 " line" 不染色，差异段染红/绿
    delete_kinds = [kind for _text, kind in delete_row["segments"]]
    insert_kinds = [kind for _text, kind in insert_row["segments"]]
    assert KIND_EQUAL in delete_kinds and KIND_DELETE in delete_kinds
    assert KIND_EQUAL in insert_kinds and KIND_INSERT in insert_kinds
    # 相同后缀保留为未染色段
    suffix = [text for text, kind in delete_row["segments"] if kind == KIND_EQUAL]
    assert any("line" in text for text in suffix)


def test_empty_texts() -> None:
    assert build_diff_rows("", "") == []
    rows = build_diff_rows("", "只有官网内容")
    assert _marks(rows) == ["+"]
    rows2 = build_diff_rows("只有本地内容", "")
    assert _marks(rows2) == ["-"]


def test_html_escapes_special_chars() -> None:
    rows = build_diff_rows("a<b>&c", "a")
    html = rows_to_html(rows)
    assert "&lt;b&gt;" in html
    assert "&amp;" in html
    assert "<b>" not in html


def test_rows_to_html_contains_markers() -> None:
    rows = build_diff_rows("旧内容", "新内容\n额外行")
    html = rows_to_html(rows)
    assert ">" + KIND_DELETE + "<" not in html  # 不直接暴露 kind
    assert "+" in html and "-" in html
    assert "background-color: #fde8e8" in html or "background-color: #e4f5e8" in html