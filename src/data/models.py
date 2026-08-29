"""
名将杀 Agent - 数据模型

定义项目核心数据模型，使用 Pydantic 进行类型校验。
官方网站数据通过 validation_alias（中文字段名）映射到模型字段。
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Annotated
from pydantic import BaseModel, Field, field_validator, model_validator


MAX_SKILL_TEXT_LENGTH = 4_000
MAX_GUIDE_TEXT_LENGTH = 20_000
MAX_GUIDE_SUMMARY_TEXT_LENGTH = 1_000
MAX_GUIDE_LIST_LENGTH = 20
GuideListItem = Annotated[str, Field(max_length=MAX_GUIDE_SUMMARY_TEXT_LENGTH)]


# ============================================================
# 枚举定义
# ============================================================

class Gender(str, Enum):
    """性别"""
    MALE = "男"
    FEMALE = "女"


class Difficulty(int, Enum):
    """武将难度评级 1-5"""
    EASY = 1
    MEDIUM = 2
    HARD = 3
    EXPERT = 4
    MASTER = 5


class ViabilityTier(str, Enum):
    """武将强度梯队"""
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"


class GameMode(str, Enum):
    """游戏模式"""
    MODE_1V1 = "1v1"
    MODE_2V2 = "2v2"
    MODE_3V3 = "3v3"
    MODE_5V5 = "5v5"
    MODE_BRAWL = "乱斗"


class CardType(str, Enum):
    """卡牌类型"""
    ACTION = "行动牌"
    STRATAGEM = "战法牌"
    EQUIPMENT = "装备牌"
    DELAYED = "延时牌"
    BASIC = "基本牌"


# ============================================================
# 基础模型
# ============================================================

class Skill(BaseModel):
    """武将技能"""
    name: str = Field(..., description="技能名称")
    description: str = Field(default="", max_length=MAX_SKILL_TEXT_LENGTH, description="技能描述")
    settlement: str = Field(default="", max_length=MAX_SKILL_TEXT_LENGTH, description="结算详情")

    @field_validator("name")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("技能名称不能为空")
        return v.strip()

class Card(BaseModel):
    """对局内基础卡牌"""
    model_config = {"populate_by_name": True}

    id: str = Field(..., validation_alias="id", description="卡牌唯一标识")
    name: str = Field(..., validation_alias="name", description="卡牌名称")
    card_type: CardType = Field(..., validation_alias="card_type", description="卡牌类型")
    card_desc: str = Field(default="", validation_alias="card_desc", description="简短描述")
    card_detail: str = Field(default="", validation_alias="card_detail", description="规则详解")
    card_amount: int = Field(default=1, ge=0, validation_alias="card_amount", description="牌堆中数量")

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("卡牌名称不能为空")
        return v.strip()


# ============================================================
# 核心业务模型
# ============================================================

class Hero(BaseModel):
    """武将基础信息"""
    model_config = {"populate_by_name": True}

    id: int = Field(..., validation_alias="角色ID", description="游戏内武将编号")
    name: str = Field(..., validation_alias="名称", description="武将中文名")
    title: str = Field(default="", description="武将称号")
    faction: str = Field(default="", validation_alias="势力", description="所属势力")
    position: str = Field(default="", validation_alias="定位", description="定位（如：输出/辅助/控制/防御）")
    max_hp: int = Field(default=4, ge=1, le=20, validation_alias="体力上限", description="体力上限")
    max_hand: int = Field(default=4, ge=0, le=20, validation_alias="手牌上限", description="手牌上限")
    gender: Gender = Field(default=Gender.MALE, validation_alias="性别", description="性别")
    skills: list[Skill] = Field(
        default_factory=list, max_length=MAX_GUIDE_LIST_LENGTH, validation_alias="技能", description="技能列表"
    )
    difficulty: Difficulty = Field(default=Difficulty.MEDIUM, description="难度评级 1-5")
    mode_viability: dict[str, ViabilityTier] = Field(
        default_factory=dict, description="各模式强度梯队"
    )
    last_updated: str = Field(
        default_factory=lambda: date.today().isoformat(),
        description="最后更新时间",
    )
    icon_url: str = Field(default="", description="武将头像 URL")

    @field_validator("name")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("字段不能为空")
        return v.strip()

    @field_validator("id")
    @classmethod
    def id_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("ID 必须为正整数")
        return v

    @field_validator("mode_viability")
    @classmethod
    def validate_mode_viability(cls, v: dict[str, ViabilityTier]) -> dict[str, ViabilityTier]:
        valid_modes = {"1v1", "2v2", "3v3", "5v5", "乱斗"}
        for key in v:
            if key not in valid_modes:
                raise ValueError(f"无效的游戏模式: {key}")
        return v


def synergy_rating_for_score(score: int) -> str:
    """根据综合评分返回相性评级。"""
    if score >= 9:
        return "S"
    if score >= 6:
        return "A"
    if score >= 3:
        return "B"
    if score >= 0:
        return "C"
    return "D"


class SynergyScore(BaseModel):
    """武将间相性评分"""
    hero_a_id: int = Field(..., description="武将A ID")
    hero_b_id: int = Field(..., description="武将B ID")
    score: int = Field(..., ge=-10, le=10, description="综合相性评分 (-10 ~ 10)")
    synergy_rating: str = Field(default="C", description="由综合评分自动推导的 S/A/B/C/D 总评")
    combo_ceiling: int = Field(default=5, ge=1, le=10, description="配合上限 1-10")
    combo_stability: int = Field(default=5, ge=1, le=10, description="配合稳定性 1-10")
    adaptability: int = Field(default=5, ge=1, le=10, description="环境适应力 1-10")
    description: str = Field(default="", max_length=MAX_SKILL_TEXT_LENGTH, description="相性总评的一句话定性判断")
    last_updated: str = Field(
        default_factory=lambda: date.today().isoformat(),
        description="最后成功生成相性评分的日期",
    )

    @field_validator("hero_a_id", "hero_b_id")
    @classmethod
    def id_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("ID 必须为正整数")
        return v

    @field_validator("synergy_rating")
    @classmethod
    def validate_synergy_rating(cls, v: str) -> str:
        if v.upper() not in {"S", "A", "B", "C", "D"}:
            raise ValueError("synergy_rating 必须为 S/A/B/C/D")
        return v.upper()

    @model_validator(mode="after")
    def validate_pair_and_rating(self) -> "SynergyScore":
        if self.hero_a_id == self.hero_b_id:
            raise ValueError("相性双方不能是同一武将")
        self.synergy_rating = synergy_rating_for_score(self.score)
        return self


class Combo(BaseModel):
    """实战配队（外部工具导出的社区实战组合，只读数据集，由导入脚本维护）"""
    hero1_name: str = Field(..., min_length=1, description="武将1 名称（与导出一致）")
    hero2_name: str = Field(..., min_length=1, description="武将2 名称")
    hero1_id: int = Field(..., description="武将1 角色 ID（导入时按名称映射）")
    hero2_id: int = Field(..., description="武将2 角色 ID")
    rating: int = Field(..., ge=1, le=10, description="实战评级 1-10")
    position: str = Field(default="both", description="配对级座位摘要（如 both/14/23，不含顺序）")
    note: str = Field(default="", description="手录备注（座次顺序的权威来源，界面原文展示）")
    hero1_seats: list[int] = Field(default_factory=list, description="武将1 可坐号位（空 = 无座次要求）")
    hero2_seats: list[int] = Field(default_factory=list, description="武将2 可坐号位（空 = 无座次要求）")

    @field_validator("hero1_id", "hero2_id")
    @classmethod
    def id_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("ID 必须为正整数")
        return v

    @field_validator("hero1_seats", "hero2_seats")
    @classmethod
    def validate_seats(cls, v: list[int]) -> list[int]:
        if any(not 1 <= s <= 4 for s in v):
            raise ValueError("座次号位必须是 1-4")
        return sorted(v)

    @model_validator(mode="after")
    def validate_pair(self) -> "Combo":
        if self.hero1_id == self.hero2_id:
            raise ValueError("配队双方不能是同一武将")
        return self


class HeroGuide(BaseModel):
    """武将攻略"""
    hero_id: int = Field(..., description="武将 ID")
    key_points: list[GuideListItem] = Field(default_factory=list, max_length=MAX_GUIDE_LIST_LENGTH, description="操作要点")
    weak_against_type: list[GuideListItem] = Field(
        default_factory=list, max_length=MAX_GUIDE_LIST_LENGTH, description="克制该武将的类型"
    )
    strong_against_type: list[GuideListItem] = Field(
        default_factory=list, max_length=MAX_GUIDE_LIST_LENGTH, description="该武将克制的类型"
    )
    synergizes_with: list[int] = Field(
        default_factory=list, max_length=MAX_GUIDE_LIST_LENGTH, description="与谁搭配好（武将 ID 列表）"
    )
    counter_strategy: str = Field(default="", max_length=MAX_GUIDE_SUMMARY_TEXT_LENGTH, description="面对该武将的对抗建议")
    description: str = Field(default="", max_length=MAX_GUIDE_TEXT_LENGTH, description="攻略正文")
    tips_for_beginners: str = Field(default="", max_length=MAX_GUIDE_SUMMARY_TEXT_LENGTH, description="新手提示")
    last_updated: str = Field(
        default_factory=lambda: date.today().isoformat(),
        description="最后更新时间",
    )


# ============================================================
# 增量更新模型
# ============================================================

class IncrementalUpdate(BaseModel):
    """增量更新结构 —— 用于每周数据更新"""
    version: str = Field(default="1.0", description="更新版本号")
    update_date: str = Field(
        default_factory=lambda: date.today().isoformat(),
        description="更新日期",
    )
    added_heroes: list[Hero] = Field(default_factory=list, description="新增武将")
    modified_heroes: list[Hero] = Field(default_factory=list, description="修改的武将")
    removed_hero_ids: list[int] = Field(default_factory=list, description="删除的武将 ID")
    added_synergies: list[SynergyScore] = Field(default_factory=list, description="新增相性评分")
    modified_synergies: list[SynergyScore] = Field(default_factory=list, description="修改的相性评分")
    removed_synergy_ids: list[tuple[int, int]] = Field(
        default_factory=list, description="删除的相性对 (hero_a_id, hero_b_id)"
    )
    added_guides: list[HeroGuide] = Field(default_factory=list, description="新增攻略")
    modified_guides: list[HeroGuide] = Field(default_factory=list, description="修改的攻略")
    removed_guide_ids: list[int] = Field(default_factory=list, description="删除的攻略 ID")
