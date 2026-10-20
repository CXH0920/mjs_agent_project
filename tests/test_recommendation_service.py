"""选将推荐数据服务测试。"""

import pytest
from src.business.analysis.recommendation_service import RecommendationService
from src.data.recommendation_index_repository import RecommendationIndex


def test_service_loads_one_snapshot_with_win_rate_percent() -> None:
    index = RecommendationIndex(
        1, "曹操", 0.60, 1, 1, 1.0, 1.0, 1.0, 1.0, 1.0, 100, "S", 1, "有效",
    )
    service = RecommendationService(
        load_win_rates_fn=lambda: {"曹操": 60.0, "刘备": 55.0, "孙权": 58.0},
        load_indexes_fn=lambda: {"曹操": index},
        refresh_indexes_fn=lambda: {"曹操": index},
    )

    data = service.load()

    assert data.indexes == {"曹操": index}
    # 胜率以 0–100 百分数暴露，与 RecommendationIndex.win_rate(0–1) 量纲区分
    assert data.win_rate_percent == {"曹操": 60.0, "刘备": 55.0, "孙权": 58.0}
    assert data.load_error is None


def test_service_reports_index_read_failure_instead_of_empty_snapshot() -> None:
    """读取失败必须与"确实没有数据"区分，否则用户会失去重建入口。"""

    def broken_load():
        raise PermissionError("文件被占用")

    service = RecommendationService(
        load_win_rates_fn=lambda: {"曹操": 60.0},
        load_indexes_fn=broken_load,
    )

    data = service.load()

    assert data.indexes == {}
    assert data.win_rate_percent == {"曹操": 60.0}
    assert data.load_error is not None and "文件被占用" in data.load_error


def test_service_marks_indexes_stale_and_rebuild_reads_back_real_state() -> None:
    """重建后以回读状态为准：数据层清过标记才算新鲜。"""
    states: list[bool] = []

    def rebuild_clearing_stale():
        states.append(False)  # 模拟数据层 refresh 写入规范路径时自清标记
        return {}

    service = RecommendationService(
        load_win_rates_fn=dict,
        load_indexes_fn=dict,
        refresh_indexes_fn=rebuild_clearing_stale,
        is_stale_fn=lambda: bool(states and states[-1]),
        mark_stale_fn=states.append,
    )

    service.mark_indexes_stale()
    assert service.load().indexes_stale is True
    assert service.rebuild_indexes().indexes_stale is False
    assert states == [True, False]


def test_rebuild_does_not_fake_freshness_when_data_layer_kept_stale() -> None:
    """回归 VUL-B4-001：数据层未清标记（如写入非规范路径）时不得宣告新鲜。"""
    states: list[bool] = [True]
    service = RecommendationService(
        load_win_rates_fn=dict,
        load_indexes_fn=dict,
        refresh_indexes_fn=dict,  # 不清标记
        is_stale_fn=lambda: bool(states and states[-1]),
        mark_stale_fn=states.append,
    )

    assert service.rebuild_indexes().indexes_stale is True


def test_rebuild_does_not_write_stale_state_itself() -> None:
    """回归 VUL-B4-001：业务层不再重复写标记（避免二次原子写失败被误报）。"""
    writes: list[bool] = []
    service = RecommendationService(
        load_win_rates_fn=dict,
        load_indexes_fn=dict,
        refresh_indexes_fn=dict,
        is_stale_fn=lambda: False,
        mark_stale_fn=writes.append,
    )

    service.rebuild_indexes()

    assert writes == []


def test_load_propagates_unexpected_error() -> None:
    """非 OSError 的意外异常仍应上抛，由调用方兜底。"""

    def broken_load():
        raise ValueError("结构异常")

    service = RecommendationService(
        load_win_rates_fn=dict,
        load_indexes_fn=broken_load,
    )

    with pytest.raises(ValueError):
        service.load()
