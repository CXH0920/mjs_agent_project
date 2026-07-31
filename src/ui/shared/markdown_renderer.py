"""受限 Markdown 到 HTML 渲染。"""

from __future__ import annotations

import mistune

from src.data.models import MAX_GUIDE_TEXT_LENGTH

_markdown = mistune.create_markdown(escape=True)


def render_markdown(text: str) -> str:
    """渲染长度受限且转义原始 HTML 的 Markdown。"""
    if not text:
        return ""
    if len(text) > MAX_GUIDE_TEXT_LENGTH:
        return "<p>内容过长，无法显示。</p>"
    return _markdown(text)
