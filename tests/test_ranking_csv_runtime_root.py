"""榜单与抓取数据的读写根守护：打包态（BUNDLE_ROOT≠PROJECT_ROOT）下全部落可写根。

曾发生的缺陷：官方榜单导入与 scraper 抓取写 PROJECT_ROOT/data（exe 同级），
而三个数据仓库读取钉死 BUNDLE_ROOT/data（只读 _internal）——开发态两根相等
无感，打包态用户导入的新榜单从未被算法读取（win_rate_repository.py 旧注释
"fallback 后续再加"）。开发态两根天然相等、CI 检出无 data CSV（抓取数据经
私有仓同步不入库），故用 monkeypatch 双根 + importlib.reload 模拟 frozen
分裂——tests/ 首次引入 reload，仅此一处、finally 还原，勿扩散。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from src.config import env

# reload 依赖序：recommendation_index / freshness 的模块级常量 import 自前两者
_REPO_MODULES = (
    "src.data.win_rate_repository",
    "src.data.peak_win_rate_repository",
    "src.data.recommendation_index_repository",
    "src.business.freshness",
)
_SCRAPER_MODULES = (
    "src.scraper.official_source.full",
    "src.scraper.official_source.incremental",
    "src.scraper.ai.batch",
    "src.scraper.ai.rule_summary",
)


def _reload_all() -> None:
    for name in _REPO_MODULES + _SCRAPER_MODULES:
        importlib.reload(importlib.import_module(name))


@pytest.fixture()
def split_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """模拟 frozen 根分裂：bundle（只读基线）与 runtime（可写根）分离。"""
    bundle = tmp_path / "_internal"
    runtime = tmp_path / "runtime"
    bundle.mkdir()
    runtime.mkdir()
    monkeypatch.setattr(env, "BUNDLE_ROOT", bundle)
    monkeypatch.setattr(env, "PROJECT_ROOT", runtime)
    _reload_all()
    yield bundle, runtime
    monkeypatch.undo()
    _reload_all()  # 还原真实根，避免污染后续测试的模块级常量


def test_ranking_read_paths_follow_writable_root(split_roots) -> None:
    """六个榜单 CSV、heroes.json 与推荐指数快照的读取常量必须落可写根。"""
    _bundle, runtime = split_roots
    from src.business import freshness
    from src.data import peak_win_rate_repository as peak
    from src.data import recommendation_index_repository as rec
    from src.data import win_rate_repository as win

    runtime_data = runtime / "data"
    assert win.WIN_RATE_CSV == runtime_data / "2v2胜率排行.csv"
    assert peak.PEAK_WIN_RATE_CSV == runtime_data / "巅峰赛胜率排行.csv"
    assert peak.PEAK_PICK_RANK_CSV == runtime_data / "巅峰赛出场排行.csv"
    assert rec.WIN_RATE_CSV == runtime_data / "2v2胜率排行.csv"
    assert rec.PICK_RANK_CSV == runtime_data / "2v2出场排行.csv"
    assert rec.BAN_RANK_CSV == runtime_data / "武将放逐.csv"
    assert rec.HEROES_JSON == runtime_data / "heroes.json"
    assert rec.RECOMMENDATION_INDEX_CSV == runtime_data / "武将推荐指数.csv"
    assert all(path.parent == runtime_data for path in freshness.RANKING_CSV_PATHS)


def test_scraper_default_output_paths_follow_writable_root(split_roots) -> None:
    """武将抓取与 AI 批量生成的默认输出必须落 UI 实际读取的可写根。"""
    _bundle, runtime = split_roots
    from src.scraper.ai import batch, rule_summary
    from src.scraper.official_source import full, incremental

    runtime_data = runtime / "data"
    assert full.DEFAULT_OUTPUT == runtime_data / "heroes.json"
    assert incremental.DEFAULT_DATA_DIR == runtime_data
    assert incremental.DEFAULT_HEROES_FILE == runtime_data / "heroes.json"
    assert batch.DEFAULT_DATA_DIR == runtime_data
    assert batch.DEFAULT_HEROES_FILE == runtime_data / "heroes.json"
    assert batch.DEFAULT_GUIDES_FILE == runtime_data / "guides.json"
    assert batch.DEFAULT_SYNERGIES_FILE == runtime_data / "synergies.json"
    # 核心规则摘要：检索链路（src/rag/config）读运行时根，AI 生成兜底须同根，
    # 用户对部署副本的修改才会生效
    assert rule_summary._CORE_RULES_FILE == runtime_data / "rag_corpus" / "核心规则摘要.md"
