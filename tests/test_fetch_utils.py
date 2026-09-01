"""进度协议解析测试：锁定子进程 print 行 → 结构化事件的协议契约。

样本行取自 src/scraper/ai/generation.py、api_generator.py、browser_generator.py
的实际 print 格式（含前导空格）；generation.py 重构时这些行是必须保持的协议。
"""

from __future__ import annotations

from src.business.fetching.fetch_utils import parse_generation_event


def test_start_lines_hero_and_pair_format():
    """武将与配对两种 START 行（含生产格式的前导空格）都解析出条目名与计数。"""
    event = parse_generation_event("[12/80] 诸葛亮 START")
    assert (event.kind, event.label, event.current, event.total) == ("start", "诸葛亮", 12, 80)

    pair = parse_generation_event("  [3/9] 荆轲 <-> 典韦 START")
    assert (pair.kind, pair.label, pair.current, pair.total) == ("start", "荆轲 <-> 典韦", 3, 9)


def test_ok_line_with_score_suffix():
    """OK 行携带评分后缀时仍取条目名与计数。"""
    event = parse_generation_event("[1/3] 甲 <-> 乙 OK - 评分: 8")
    assert (event.kind, event.label, event.current, event.total) == ("ok", "甲 <-> 乙", 1, 3)


def test_fail_and_skip_lines():
    event = parse_generation_event("[2/3] 甲 <-> 丙 FAIL")
    assert (event.kind, event.label) == ("fail", "甲 <-> 丙")

    skip = parse_generation_event("[3/3] 甲 <-> 丁 SKIP（已有相性）")
    assert (skip.kind, skip.label) == ("skip", "甲 <-> 丁")


def test_retry_line_with_real_http_format():
    """限流退避行（api_generator 实际格式）解析出轮次与等待秒数。"""
    event = parse_generation_event("  [重试] HTTP 429，第 2/3 次，4 秒后重试")
    assert event.kind == "retry"
    assert event.label == "HTTP 429"
    assert (event.retry_round, event.retry_max, event.wait_seconds) == (2, 3, 4)


def test_rest_line_with_log_prefix_and_ellipsis():
    """浏览器模式经日志转发后带时间戳前缀与省略号，仍按 search 语义解析。"""
    event = parse_generation_event(
        "2026-07-27 [INFO] src.scraper.ai.browser_generator: [休息] 随机休息 126 秒..."
    )
    assert (event.kind, event.wait_seconds) == ("rest", 126)

    plain = parse_generation_event("  [休息] 随机休息 45 秒...")
    assert (plain.kind, plain.wait_seconds) == ("rest", 45)


def test_non_protocol_lines_return_none():
    """日志正文、RAG 行、未知状态行都不产生事件（UI 走原样展示兜底）。"""
    assert parse_generation_event("[RAG] 注入 3 块语料") is None
    assert parse_generation_event("2026-07-31 [ERROR] src.scraper.ai: 原始回复：敏感正文") is None
    assert parse_generation_event("[1/3] 甲 <-> 乙 RUNNING") is None
    assert parse_generation_event("") is None


def test_kind_priority_when_line_contains_multiple_markers():
    """顺序与旧对话框一致：START 优先于其他标记，OK 先于 FAIL/SKIP 判定。"""
    event = parse_generation_event("[1/2] 甲 OK START")
    assert event.kind == "start"

    event2 = parse_generation_event("[1/2] 甲 OK FAIL")
    assert event2.kind == "ok"
