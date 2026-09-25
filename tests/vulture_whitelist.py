# -*- coding: utf-8 -*-
"""vulture 白名单：框架回调与数据契约符号，被引用即视为"使用"。

仅收录两类：
1. 框架（Qt / urllib / pydantic）按名字调用、代码里无显式调用点的覆写；
2. 数据契约符号——pydantic/dataclass 模型字段与枚举成员：由 JSON 反序列化、
   构造器关键字或 getattr(field_name) 动态读取，vulture 无法看见这些使用。
与 vulture 命令一起扫描：
    python -m vulture src tests/vulture_whitelist.py \
        --min-confidence 60 --ignore-decorators "@field_validator,@model_validator"
注意：白名单按裸名字匹配（全仓生效），新增条目需评估与普通函数/变量的撞名遮蔽风险。
"""

# ── 框架回调 ──────────────────────────────────────────────────────
redirect_request  # urllib.request.HTTPRedirectHandler 逐跳拦截重定向（crawler._NoRedirectHandler）
itemAt  # QLayout 抽象方法（widgets.FlowLayout）
expandingDirections  # QLayout 抽象方法（widgets.FlowLayout）
hasHeightForWidth  # QLayout 抽象方法（widgets.FlowLayout）
heightForWidth  # QLayout 抽象方法（widgets.FlowLayout）
mouseMoveEvent  # QWidget 事件回调（roi_selector）
paintEvent  # QWidget 事件回调（roi_selector）
disambiguation  # QTranslator.translate 覆写签名参数（chinese_translator）

# ── pydantic 模型字段（JSON 反序列化 / 属性读取契约）────────────────
mode_viability  # Hero.mode_viability，官网数据/验证器
update_date  # IncrementalUpdate.update_date
function  # SpecialCardItem.function，特殊牌 JSON 字段
stackable  # SpecialCardItem.stackable
model_config  # pydantic 配置属性（populate_by_name）

# ── DTO / dataclass 字段（构造器关键字写入，外部读取契约）──────────
source_field  # match_analysis_service 三个提示 DTO 的来源标注字段
weight  # PeakBanAdvice.weight，文档化 DTO 契约
is_main  # MuMuDeviceInfo.is_main
first_seen_at  # Announcement.first_seen_at
checked_at  # BaikeSnapshot / CardSyncState.checked_at

# ── 枚举成员（值来自 JSON / 数据文件，成员名在代码中不经引用）──────
EASY  # Difficulty.EASY
HARD  # Difficulty.HARD
EXPERT  # Difficulty.EXPERT
MASTER  # Difficulty.MASTER
T0  # ViabilityTier.T0
T1  # ViabilityTier.T1
T2  # ViabilityTier.T2
T3  # ViabilityTier.T3
T4  # ViabilityTier.T4
ACTION  # CardType.ACTION
STRATAGEM  # CardType.STRATAGEM
EQUIPMENT  # CardType.EQUIPMENT
DELAYED  # CardType.DELAYED
BASIC  # CardType.BASIC

# ── 动态读取字段（refinement_session.getattr(update, field)）───────
trigger_condition  # RefinementUpdate 精化字段
special_rules  # RefinementUpdate 精化字段

# ── 配置回显预留（项目既有审计决策，见 call_graph_rag.md"预留"条目）─
RAG_PROJECT_DIR  # config.env.example 预留键回显，全项目无消费点
