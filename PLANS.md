# 名将杀 Agent - 实施方案

## 项目概述

名将杀 Agent 是一个桌面端辅助工具，面向名将杀手游的轻度玩家，运行于 PC 端 MuMu 模拟器上。核心功能为**选将推荐**和**武将数据库查询**。

---

## 一、技术架构

### 整体架构（四层解耦）

遵循 OCR/自动化项目专项规范，严格分层：

```
┌──────────────────────────────────────────────┐
│                 UI 层 (PySide6)                │
│  主窗口 / 武将浏览器 / 推荐面板 / API配置     │
├──────────────────────────────────────────────┤
│             业务层 (Business)                  │
│  采集服务 / 攻略服务 / 相性服务 / 推荐引擎    │
├──────────────────────────────────────────────┤
│             数据层 (Data)                      │
│  DataFacade / Pydantic / JSON 持久化          │
├──────────────────────────────────────────────┤
│           采集层 (Capture / Scraper)           │
│  官网爬虫 / AI 批量生成 / 屏幕采集 (待开发)   │
└──────────────────────────────────────────────┘
```

### 技术栈

| 层 | 技术选型 | 理由 |
|---|---|---|
| UI | PySide6 | 用户指定，成熟可靠 |
| 数据存储 | JSON 文件 | 本地化，无需数据库，150武将量级足够 |
| 图像处理 | OpenCV | 待开发 |
| OCR | easyocr | 待开发 |
| 爬虫 | urllib + BeautifulSoup4 | 轻量同步爬取 |
| AI API | httpx → DeepSeek | 异步/同步兼容，稳定高效 |
| 数据模型 | Pydantic v2 | 类型校验 |
| 日志 | logging | 标准库，无需额外依赖 |

### 关键约束
- 推荐路径：**优先走预计算相性表查询**（实时）
- 攻略内容：AI 批量预生成 + 人工审核 → 存入本地 JSON
- 增量更新：仅更新新增/变动的武将数据

---

## 二、目录结构

```
test_project/
│
├── AGENTS.md              # 开发规范
├── PLANS.md               # 实施计划
├── CLAUDE.md              # Claude Code 上下文
├── environment.yml        # Conda 环境定义
├── README.md              # 项目文档
│
├── src/
│   ├── main.py                  # 应用入口
│   ├── config/
│   │   └── env.py               # .env 解析/加载/保存
│   │
│   ├── data/                    # 数据层
│   │   ├── models.py            # Pydantic 数据模型
│   │   ├── manager.py           # DataFacade 门面 + 增量更新
│   │   ├── hero_manager.py      # Hero CRUD + JSON
│   │   ├── synergy_manager.py   # SynergyScore CRUD + JSON
│   │   └── guide_manager.py     # HeroGuide CRUD + JSON
│   │
│   ├── scraper/                 # 数据采集
│   │   ├── crawler.py           # 爬虫核心（公开 API）
│   │   ├── official.py          # 官网全量爬虫
│   │   ├── incremental.py       # 增量/指定爬虫
│   │   ├── ai_utils.py          # AI 生成共享工具函数
│   │   ├── ai_generator.py      # AIBatchGenerator（API 调用/限速/Prompt 构建/校验）
│   │   ├── ai_batch.py          # CLI 入口（纯编排，不含业务逻辑）
│   │   ├── ai_guide.py          # 攻略生成循环
│   │   ├── ai_synergy.py        # 全量相性生成循环
│   │   ├── ai_synergy_pair.py   # 指定两武将相性配对生成
│   │   └── ai_synergy_single.py # 选定武将 x 全体相性生成
│   │
│   ├── business/                # 业务层（QProcess 管理 + 信号通知）
│   │   ├── fetch_service.py     # 武将采集业务服务
│   │   ├── guide_fetch_service.py # 攻略生成业务服务
│   │   └── synergy_fetch_service.py # 相性获取业务服务
│   │
│   ├── capture/                 # 采集层（待开发）
│   │   └── __init__.py
│   │
│   └── ui/                      # UI 层
│       ├── style.py             # 全局样式表（天蓝色调）
│       ├── main_window.py       # 主窗口
│       ├── hero_browser.py      # 武将浏览
│       ├── settings_dialog.py   # API 配置对话框
│       ├── fetch_dialog.py      # 武将获取选择
│       ├── guide_fetch_dialog.py # 攻略获取选择
│       ├── synergy_pair_dialog.py # 相性指定获取（选 2 武将）
│       ├── synergy_single_dialog.py # 相性选定武将（选 1 武将）
│       ├── cost_confirm_dialog.py   # 成本确认
│       └── guide_progress_dialog.py # 进度条
│
├── data/                        # 本地 JSON 数据文件
│   ├── heroes.json              # 149 个武将
│   ├── synergies.json           # 相性评分
│   ├── guides.json              # 武将攻略
│   └── cards.json               # 基础卡牌数据
│
├── tests/
│   ├── test_models.py           # 25 tests
│   ├── test_ai_batch.py         # 33 tests
│   ├── test_hero_manager.py     # 13 tests
│   ├── test_synergy_manager.py  # 13 tests
│   ├── test_guide_manager.py    # 11 tests
│   ├── test_incremental_update.py # 8 tests
│   └── test_ui.py               # 4 tests
│
└── docs/
    ├── field_mapping.md
    └── prompts/
        ├── hero_guide.md
        └── synergy_score.md
```

---

## 三、数据模型设计

详见 `src/data/models.py`，核心模型包括：

### Hero（武将）

采用 Pydantic 模型 + 中文 JSON 别名映射，通过 `Field(validation_alias=...)` 实现。

```python
class Skill(BaseModel):
    name: str
    description: str = ""
    settlement: str = ""

class Hero(BaseModel):
    id: int
    name: str
    title: str = ""
    faction: str
    position: str
    max_hp: int = 4
    max_hand: int = 4
    gender: Gender = Gender.MALE
    skills: list[Skill] = []
    difficulty: Difficulty = Difficulty.MEDIUM
    mode_viability: dict[str, ViabilityTier] = {}
    last_updated: str
```

### SynergyScore（相性评分）

```python
class SynergyScore(BaseModel):
    hero_a_id: int
    hero_b_id: int
    score: int = Field(ge=-10, le=10)
    synergy_rating: str = "C"     # S/A/B/C/D
    combo_ceiling: int = Field(default=5, ge=1, le=10)
    combo_stability: int = Field(default=5, ge=1, le=10)
    adaptability: int = Field(default=5, ge=1, le=10)
    description: str = ""
```

### HeroGuide（攻略）

```python
class HeroGuide(BaseModel):
    hero_id: int
    key_points: list[str] = []
    counters: list[int] = []
    synergizes_with: list[int] = []
    description: str = ""
    tips_for_beginners: str = ""
    last_updated: str
```

---

## 四、AI 批量生成模块层次

```
src/scraper/ai_batch.py              CLI 入口（仅编排，不含业务逻辑）
    ├── src/scraper/ai_utils.py      共享工具函数
    ├── src/scraper/ai_generator.py  API 调用核心
    ├── src/scraper/ai_guide.py      攻略生成
    ├── src/scraper/ai_synergy.py    全量相性生成
    ├── src/scraper/ai_synergy_pair.py   指定配对生成
    └── src/scraper/ai_synergy_single.py 选定武将生成
```

- 每个生成模块是独立函数，通过参数接收依赖
- 无循环导入，全单向调用
- 向后兼容：`from src.scraper.ai_batch import AIBatchGenerator` 依然有效

---

## 五、任务拆分与执行计划

### 阶段一：项目脚手架与数据模型

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| 1.1 | 初始化 Conda 环境 | environment.yml + 环境 | ✅ 已完成 |
| 1.2 | 创建目录结构 | 完整目录树 | ✅ 已完成 |
| 1.3 | 实现数据模型 | models.py | ✅ 已完成 |
| 1.4 | 实现数据管理器 | manager.py（JSON 读写/增量更新） | ✅ 已完成 |
| 1.5 | 准备样本数据 | 149个武将 + 6条相性 + 4份攻略的 JSON 样本 | ✅ 已完成 |

### 阶段二：数据采集工具

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| 2.1 | 官网结构探查 | docs/field_mapping.md | ✅ 已完成 |
| 2.2 | 爬虫实现 + 数据清洗 | crawler.py + official.py + incremental.py | ✅ 已完成 |
| 2.3 | 攻略生成 Prompt 设计 | docs/prompts/hero_guide.md | ✅ 已完成 |
| 2.4 | 相性评分 Prompt 设计 | docs/prompts/synergy_score.md | ✅ 已完成 |
| 2.5 | AI 批量生成基础框架 | ai_batch.py + ai_generator.py + ai_guide.py + ai_synergy.py + ai_utils.py | ✅ 已完成 |

### 阶段三：桌面应用 UI

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| 3.1 | 主窗口框架 | main_window.py | ✅ 已完成 |
| 3.2 | 武将浏览器 | hero_browser.py | ✅ 已完成 |
| 3.3 | API 配置对话框 | settings_dialog.py | ✅ 已完成 |
| 3.4 | 应用入口集成 | main.py | ✅ 已完成 |
| 3.5 | 武将/攻略获取菜单 + 信号系统 | main_window.py + fetch_service.py + guide_fetch_service.py | ✅ 已完成 |
| 3.6 | 攻略进度条/成本确认 | guide_progress_dialog.py + cost_confirm_dialog.py | ✅ 已完成 |
| 3.7 | 全局样式表 | style.py | ✅ 已完成 |
| 3.8 | 武将获取/攻略获取对话框 | fetch_dialog.py + guide_fetch_dialog.py | ✅ 已完成 |

### 阶段四：相性交互获取

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| 4.1 | 相性指定获取对话框 | synergy_pair_dialog.py（选 2 武将） | ✅ 已完成 |
| 4.2 | 相性选定武将对话框 | synergy_single_dialog.py（选 1 武将） | ✅ 已完成 |
| 4.3 | 相性配对生成 CLI 模式 | ai_synergy_pair.py（--synergy-pair） | ✅ 已完成 |
| 4.4 | 相性单武将生成 CLI 模式 | ai_synergy_single.py（--synergy-single） | ✅ 已完成 |
| 4.5 | 相性获取业务服务 | synergy_fetch_service.py（QProcess） | ✅ 已完成 |
| 4.6 | "武将相性"菜单集成 | main_window.py（选定武将 + 指定获取） | ✅ 已完成 |

### 阶段五：屏幕采集与识别

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| 5.1 | MuMu 模拟器截图 | screen.py | ⏳ 待开发 |
| 5.2 | 轮廓检测选将框 | detector.py | ⏳ 待开发 |
| 5.3 | OCR 识别武将名 | ocr.py | ⏳ 待开发 |

### 阶段六：推荐引擎

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| 6.1 | 相性查询引擎 | recommendation.py | ⏳ 待开发 |
| 6.2 | 推荐结果展示集成 | "选将推荐" Tab | ⏳ 待开发 |

---

## 六、风险分析

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| MuMu 截屏延迟 | 体验下降 | 使用 ADB 截图，延迟<100ms |
| OCR 识别武将名不准 | 推荐失败 | 结合轮廓位置 + 名称列表过滤 |
| 新武将每周更新，需持续维护 | 维护成本 | 增量更新机制 + 定时提醒 |
| 游戏UI改版 | 采集失效 | 轮廓检测参数配置化，易适配 |
| 150 武将全量相性组合多 | 生成成本高 | 设定分数下限过滤 + 支持单武将配对生成 |

---

## 七、开发原则（执行中遵循）

1. **先写测试**：数据模型和业务层先写单元测试
2. **小步迭代**：每个阶段完成后保持可运行状态
3. **配置化坐标**：所有屏幕坐标在采集层配置化，禁止硬编码
4. **类型注解**：所有函数使用 Python typing 类型标注
5. **日志完整**：各层均使用 logging 记录关键操作
6. **遵守 AGENTS.md**：遵循根目录 AGENTS.md 中的规范
