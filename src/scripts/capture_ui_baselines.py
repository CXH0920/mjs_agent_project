"""生成阶段五、六的确定性 UI 视觉基线。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from src.config.env import PROJECT_ROOT

from PySide6.QtWidgets import QApplication

from src.business.analysis.recommendation_service import RecommendationData
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero, HeroGuide, SynergyScore
from src.data.recommendation_index_repository import RecommendationIndex
from src.data.synergy_manager import SynergyManager
from src.ui.app.main_window import MainWindow
from src.ui.shared.style import GLOBAL_STYLE


SIZES = ((960, 640), (1100, 760), (1440, 900))
HERO_SPECS = (
    (1, "刘备", "蜀", "辅助"),
    (2, "关羽", "蜀", "输出"),
    (3, "华佗", "群", "治疗"),
    (4, "典韦", "魏", "爆发"),
    (5, "刘邦", "汉", "控制"),
    (6, "乐毅", "燕", "防御"),
    (7, "公孙瓒", "群", "突进"),
    (8, "凌统", "吴", "输出"),
)


class _StaticRecommendationService:
    def __init__(self, data: RecommendationData) -> None:
        self._data = data

    def load(self) -> RecommendationData:
        return self._data


def _build_managers() -> tuple[HeroManager, SynergyManager, GuideManager]:
    heroes = HeroManager()
    heroes._items = {
        hero_id: Hero(
            id=hero_id,
            name=name,
            faction=faction,
            position=position,
        )
        for hero_id, name, faction, position in HERO_SPECS
    }

    synergies = SynergyManager()
    synergy_items = (
        SynergyScore(hero_a_id=1, hero_b_id=2, score=9, description="攻守衔接稳定"),
        SynergyScore(hero_a_id=1, hero_b_id=3, score=7, description="续航能力良好"),
        SynergyScore(hero_a_id=4, hero_b_id=5, score=8, description="控制后集中输出"),
        SynergyScore(hero_a_id=6, hero_b_id=8, score=6, description="防守与反击兼顾"),
    )
    synergies._items = {
        tuple(sorted((item.hero_a_id, item.hero_b_id))): item
        for item in synergy_items
    }

    guides = GuideManager()
    guides._items = {
        hero_id: HeroGuide(
            hero_id=hero_id,
            key_points=[f"保持{name}的核心节奏，优先完成本回合关键行动。"],
            counter_strategy=f"观察{name}的关键资源，在其启动前保留限制手段。",
            tips_for_beginners="先保证基础收益，再根据敌方手牌调整行动顺序。",
        )
        for hero_id, name, _faction, _position in HERO_SPECS[:-1]
    }
    return heroes, synergies, guides


def _recommendation_data() -> RecommendationData:
    rates = {
        name: 59.8 - index * 1.35
        for index, (_hero_id, name, _faction, _position) in enumerate(HERO_SPECS)
    }
    indexes = {
        name: RecommendationIndex(
            hero_id,
            name,
            rates[name] / 100,
            index + 1,
            index + 3,
            1.0,
            0.8,
            1.2,
            0.7,
            0.65,
            92 - index * 4,
            "S" if index < 2 else "A" if index < 5 else "B",
            index + 1,
            "有效",
        )
        for index, (hero_id, name, _faction, _position) in enumerate(HERO_SPECS)
    }
    return RecommendationData(rates, indexes, False)


def _capture(window: MainWindow, output_dir: Path, prefix: str, view: str) -> None:
    app = QApplication.instance()
    assert app is not None
    for width, height in SIZES:
        window.resize(width, height)
        window.show()
        app.processEvents()
        path = output_dir / f"{prefix}-{width}x{height}-{view}.png"
        if not window.grab().save(str(path), "PNG"):
            raise RuntimeError(f"无法保存截图：{path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "docs/ui_baseline",
        help="截图输出目录",
    )
    parser.add_argument("--prefix", default="after-workspaces", help="文件名前缀")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(GLOBAL_STYLE)
    heroes, synergies, guides = _build_managers()
    window = MainWindow(heroes, synergies, guides)

    window._tabs.setCurrentWidget(window._recommendation)
    _capture(window, args.output_dir, args.prefix, "recommendation-empty")
    window._tabs.setCurrentWidget(window._match_guide)
    _capture(window, args.output_dir, args.prefix, "match-empty")

    recommendation_data = _recommendation_data()
    window._recommendation._recommendation_service = _StaticRecommendationService(
        recommendation_data
    )
    window._recommendation.update_recommendations([
        {"index": index, "name": name, "resolution": "exact"}
        for index, (_hero_id, name, _faction, _position) in enumerate(HERO_SPECS, 1)
    ])
    window._recommendation._set_page_status("基准数据 · 已识别 8 名武将")
    window._tabs.setCurrentWidget(window._recommendation)
    _capture(window, args.output_dir, args.prefix, "recommendation-results")

    match_names = ("刘备", "关羽", "华佗", "典韦")
    window._match_guide.load_from_ocr([
        {"index": 1, "name": match_names[2], "team": "楚军"},
        {"index": 2, "name": match_names[3], "team": "楚军"},
        {"index": 4, "name": match_names[1], "team": "汉军"},
        {"index": 5, "name": match_names[0], "team": "汉军"},
    ])
    window._match_guide._win_rates = {
        name: recommendation_data.win_rates[name] for name in match_names
    }
    window._match_guide._render_cards()
    window._match_guide._confirm_lineup()
    window._match_guide._action_bar.set_status("基准数据 · 阵容已确认", "success")
    window._tabs.setCurrentWidget(window._match_guide)
    _capture(window, args.output_dir, args.prefix, "match-confirmed")

    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
