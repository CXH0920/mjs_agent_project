"""榜单与抓取数据的读写根守护：打包态（BUNDLE_ROOT≠PROJECT_ROOT）下全部落可写根。

曾发生的缺陷：官方榜单导入与 scraper 抓取写 PROJECT_ROOT/data（exe 同级），
而三个数据仓库读取钉死 BUNDLE_ROOT/data（只读 _internal）——开发态两根相等
无感，打包态用户导入的新榜单从未被算法读取（win_rate_repository.py 旧注释
"fallback 后续再加"）。开发态两根天然相等、CI 检出无 data CSV（抓取数据经
私有仓同步不入库），故用 monkeypatch 双根模拟 frozen 分裂。

路径消费已改为调用时求值（config.env.runtime_root / runtime_data_dir）：
monkeypatch 双根后直接调用断言，不再需要 importlib.reload 重拍模块快照
（tests 中 reload 范式仅存 test_runtime_root_config_and_portrait 一处）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from src.business import freshness
from src.config import env
from src.data import (
    peak_win_rate_repository as peak,
)
from src.data import (
    recommendation_index_repository as rec,
)
from src.data import (
    win_rate_repository as win,
)
from src.scraper.ai import batch, rule_summary
from src.scraper.official_source import full, incremental


@pytest.fixture()
def split_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """模拟 frozen 根分裂：bundle（只读基线）与 runtime（可写根）分离。"""
    bundle = tmp_path / "_internal"
    runtime = tmp_path / "runtime"
    bundle.mkdir()
    runtime.mkdir()
    monkeypatch.setattr(env, "BUNDLE_ROOT", bundle)
    monkeypatch.setattr(env, "PROJECT_ROOT", runtime)
    yield bundle, runtime


def test_ranking_read_paths_follow_writable_root(split_roots) -> None:
    """六个榜单 CSV、heroes.json、推荐指数快照与状态、监听目录必须落可写根。"""
    _bundle, runtime = split_roots
    runtime_data = runtime / "data"
    assert win.win_rate_csv() == runtime_data / "2v2胜率排行.csv"
    assert peak.peak_win_rate_csv() == runtime_data / "巅峰赛胜率排行.csv"
    assert peak.peak_pick_rank_csv() == runtime_data / "巅峰赛出场排行.csv"
    assert rec.win_rate_csv() == runtime_data / "2v2胜率排行.csv"
    assert rec.pick_rank_csv() == runtime_data / "2v2出场排行.csv"
    assert rec.ban_rank_csv() == runtime_data / "武将放逐.csv"
    assert rec.heroes_json() == runtime_data / "heroes.json"
    assert rec.recommendation_index_csv() == runtime_data / "武将推荐指数.csv"
    assert rec.recommendation_index_state_file() == runtime_data / "武将推荐指数状态.json"
    assert all(path.parent == runtime_data for path in freshness.ranking_csv_paths())
    # 监听目录与榜单读取同根（旧 reload 版守护未覆盖的盲点，函数化后纳入）
    assert freshness.watch_dir() == runtime_data


def test_scraper_default_output_paths_follow_writable_root(split_roots) -> None:
    """武将抓取与 AI 批量生成的默认输出必须落 UI 实际读取的可写根。"""
    _bundle, runtime = split_roots
    runtime_data = runtime / "data"
    assert full.default_output() == runtime_data / "heroes.json"
    assert incremental.default_data_dir() == runtime_data
    assert incremental.default_heroes_file() == runtime_data / "heroes.json"
    assert batch.default_data_dir() == runtime_data
    assert batch.default_heroes_file() == runtime_data / "heroes.json"
    assert batch.default_guides_file() == runtime_data / "guides.json"
    assert batch.default_synergies_file() == runtime_data / "synergies.json"
    # 核心规则摘要：检索链路（src/rag/config）读运行时根，AI 生成兜底须同根，
    # 用户对部署副本的修改才会生效
    assert rule_summary._core_rules_file() == runtime_data / "rag_corpus" / "核心规则摘要.md"
