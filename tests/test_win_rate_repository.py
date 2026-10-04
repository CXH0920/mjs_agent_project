"""胜率缓存加载回归：畸形行显式跳过并告警，不再静默缩水。

胜率表/推荐指数基于该缓存计算——坏行被无声丢弃会让下游基于
悄悄缩水的数据出结果，且无任何痕迹。
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.data import win_rate_repository


def test_load_skips_malformed_row_with_warning(tmp_path: Path, caplog) -> None:
    csv_path = tmp_path / "win_rate.csv"
    csv_path.write_text(
        "武将,胜率\n曹操,52.3%\n坏行,不是数字\n刘备,48.1%\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="src.data.win_rate_repository"):
        rates = win_rate_repository.load_win_rates(csv_path)

    assert rates == {"曹操": 52.3, "刘备": 48.1}
    records = [r for r in caplog.records if "胜率行畸形已跳过" in r.message]
    assert len(records) == 1
    assert "坏行" in records[0].getMessage()
