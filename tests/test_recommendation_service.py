"""选将推荐数据服务测试。"""

from src.business.recommendation_service import RecommendationService
from src.data.recommendation_index_repository import RecommendationIndex


def test_service_loads_one_snapshot_and_ranks_valid_win_rates() -> None:
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
    assert data.rank_win_rates(["刘备", "未知", "曹操", "孙权"]) == {2: 1, 3: 2, 0: 3}


def test_service_marks_indexes_stale_and_clears_after_rebuild() -> None:
    states: list[bool] = []
    service = RecommendationService(
        load_win_rates_fn=dict,
        load_indexes_fn=dict,
        refresh_indexes_fn=dict,
        is_stale_fn=lambda: bool(states and states[-1]),
        mark_stale_fn=states.append,
    )

    service.mark_indexes_stale()
    assert service.load().indexes_stale is True
    assert service.rebuild_indexes().indexes_stale is False
    assert states == [True, False]
