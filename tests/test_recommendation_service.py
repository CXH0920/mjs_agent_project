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
