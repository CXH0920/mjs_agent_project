"""Git 风格文本 diff 的纯函数与 HTML 渲染。

规则：
- 未变化行：无标记、正常颜色
- 删除行：左侧 `-`、浅红背景
- 新增行：左侧 `+`、浅绿背景
- 修改行：红色删除行 + 绿色新增行，行内做字符级染色
  （相同前缀/后缀不染色，仅变化片段染红/绿）
"""

from __future__ import annotations

import html as html_module
from difflib import SequenceMatcher

MARK_NORMAL = " "
MARK_DELETE = "-"
MARK_INSERT = "+"

KIND_EQUAL = "equal"
KIND_DELETE = "delete"
KIND_INSERT = "insert"

DEL_BACKGROUND = "#fde8e8"
INS_BACKGROUND = "#e4f5e8"


def _char_segments(base: str, other: str, kind: str) -> list[tuple[str, str]]:
    """输出 base 相对 other 的字符级差异段：相同段 equal，差异段 kind。"""
    matcher = SequenceMatcher(None, base, other, autojunk=False)
    segments = []
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag == "equal" and i1 < i2:
            segments.append((base[i1:i2], KIND_EQUAL))
        elif tag in ("delete", "replace") and i1 < i2:
            segments.append((base[i1:i2], kind))
    return segments or [(base, kind)]


def build_diff_rows(local_text: str | None, official_text: str | None) -> list[dict]:
    """行级 diff，返回 [{mark, segments: [(text, kind)], whole}]。

    whole=True 表示整行新增/删除（渲染整行背景）；
    whole=False 表示修改行（仅差异段染色，相同部分不染色）。
    """
    a = (local_text or "").splitlines()
    b = (official_text or "").splitlines()
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    rows: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for line in a[i1:i2]:
                rows.append({"mark": MARK_NORMAL, "segments": [(line, KIND_EQUAL)], "whole": True})
        elif tag == "delete":
            for line in a[i1:i2]:
                rows.append({"mark": MARK_DELETE, "segments": [(line, KIND_DELETE)], "whole": True})
        elif tag == "insert":
            for line in b[j1:j2]:
                rows.append({"mark": MARK_INSERT, "segments": [(line, KIND_INSERT)], "whole": True})
        else:  # replace：逐对做字符级染色
            delete_lines = a[i1:i2]
            insert_lines = b[j1:j2]
            count = max(len(delete_lines), len(insert_lines))
            for index in range(count):
                a_line = delete_lines[index] if index < len(delete_lines) else ""
                b_line = insert_lines[index] if index < len(insert_lines) else ""
                if a_line and b_line:
                    rows.append({
                        "mark": MARK_DELETE,
                        "segments": _char_segments(a_line, b_line, KIND_DELETE),
                        "whole": False,
                    })
                    rows.append({
                        "mark": MARK_INSERT,
                        "segments": _char_segments(b_line, a_line, KIND_INSERT),
                        "whole": False,
                    })
                elif a_line:
                    rows.append({
                        "mark": MARK_DELETE,
                        "segments": [(a_line, KIND_DELETE)],
                        "whole": True,
                    })
                else:
                    rows.append({
                        "mark": MARK_INSERT,
                        "segments": [(b_line, KIND_INSERT)],
                        "whole": True,
                    })
    return rows


def rows_to_html(rows: list[dict]) -> str:
    """将 diff 行渲染为等宽字体的 HTML 片段。"""
    parts = ['<div style="font-family: Consolas, monospace; font-size: 13px; white-space: pre-wrap;">']
    for row in rows:
        mark = row["mark"]
        line_style = ""
        if row["whole"]:
            background = DEL_BACKGROUND if mark == MARK_DELETE else INS_BACKGROUND if mark == MARK_INSERT else ""
            if background:
                line_style = f"background-color: {background};"
        spans = []
        for text, kind in row["segments"]:
            escaped = html_module.escape(text)
            if kind == KIND_DELETE:
                spans.append(f'<span style="background-color: {DEL_BACKGROUND};">{escaped}</span>')
            elif kind == KIND_INSERT:
                spans.append(f'<span style="background-color: {INS_BACKGROUND};">{escaped}</span>')
            else:
                spans.append(escaped)
        marker_style = "color: #888;"
        if mark == MARK_DELETE:
            marker_style = "color: #c0392b; font-weight: bold;"
        elif mark == MARK_INSERT:
            marker_style = "color: #1e8449; font-weight: bold;"
        parts.append(
            f'<div style="{line_style} padding: 0 4px;">'
            f'<span style="{marker_style}">{mark}</span> '
            + "".join(spans)
            + "</div>"
        )
    parts.append("</div>")
    return "".join(parts)