"""胜率名次的单一事实源。

选将推荐页的奖牌与巅峰赛禁选建议都要把"胜率"换算成"名次"，此前两处各写
一份：一处按武将名稳定排序，另一处按输入槽位稳定排序。同分时两者结论不同，
且槽位序随 OCR 排版漂移，用户看到的是无法解释的奖牌归属。此处收敛为一份，
同分统一按武将名排序（确定性优先于"看起来随机但稳定"的槽位序）。
"""

from __future__ import annotations

from collections.abc import Iterable


def derive_win_rate_ranks(
    win_rates: dict[str, float],
    names: Iterable[str] | None = None,
) -> dict[str, int]:
    """按胜率降序推导 1-based 名次，同分按名称稳定排序。

    names 为 None 时对全表排名；给定 names 时只在该候选集内排名——选将推荐页
    的奖牌只在"本局已识别的武将之间"比较，不能拿全服榜的名次去颁奖。
    names 中不在 win_rates 里的条目（未上榜/未识别）不参与排名。
    """
    if names is None:
        candidates = list(win_rates.items())
    else:
        # 去重：调用方传入的是槽位名列表，空槽与重名会产生重复条目，
        # 重复项会被分到相邻名次并互相覆盖。
        candidates = [(name, win_rates[name]) for name in dict.fromkeys(names) if name in win_rates]
    ordered = sorted(candidates, key=lambda item: (-item[1], item[0]))
    return {name: rank for rank, (name, _rate) in enumerate(ordered, start=1)}
