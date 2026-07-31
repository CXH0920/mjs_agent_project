"""Markdown 安全渲染测试。"""

from src.data.models import MAX_GUIDE_TEXT_LENGTH
from src.ui.shared.markdown_renderer import render_markdown


def test_render_markdown_renders_markdown_and_escapes_raw_html() -> None:
    html = render_markdown("# 标题\n\n<img src=x onerror=alert(1)>")

    assert "<h1>标题</h1>" in html
    assert "&lt;img" in html
    assert "<img src=" not in html


def test_render_markdown_rejects_excessively_long_text() -> None:
    assert render_markdown("x" * (MAX_GUIDE_TEXT_LENGTH + 1)) == "<p>内容过长，无法显示。</p>"
