# 名将杀 Agent - 实施方案

## 项目概述

名将杀 Agent 是一个桌面端辅助工具，面向名将杀手游的轻度玩家，运行于 PC 端 MuMu 模拟器上。核心功能为**选将推荐**和**武将数据库查询**。

---

## 一、技术架构

### 整体架构（四层解耦）

遵循 OCR/自动化项目专项规范，严格分层：

```
┌──────────────────────────────────────────────┐
│                 UI 层 (PyQt)                   │
│  主窗口 / 武将浏览器 / 推荐面板 / API配置     │
├──────────────────────────────────────────────┤
│             业务层 (Business)                  │
│  推荐引擎 / 相性查询 / 攻略服务               │
├──────────────────────────────────────────────┤
│             数据层 (Data)                      │
│  数据模型 / JSON读写 / 增量更新               │
├──────────────────────────────────────────────┤
│           采集层 (Capture)                     │
│  屏幕截图 / 轮廓检测 / OCR识别                │
└──────────────────────────────────────────────┘
```

### 技术栈

| 层 | 技术选型 | 理由 |
|---|---|---|
| UI | PyQt6 | 用户指定，成熟可靠 |
| 数据存储 | JSON 文件 | 本地化，无需数据库，150武将量级足够 |
| 屏幕捕获 | pyautogui / mss + MuMu ADB | 跨模拟器窗口截图 |
| 图像处理 | OpenCV (轮廓检测) | 成熟，与需求一致 |
| OCR | PaddleOCR / easyocr | 识别武将名，轻量级 |
| 爬虫 | httpx + BeautifulSoup4 | 轻量同步/异步爬取 |
| 数据模型 | pydantic | 类型校验，与 Python 专项规范一致 |
| 日志 | logging | 标准库，无需额外依赖 |

### 关键约束
- 推荐路径：**优先走预计算相性表查询**（实时），若 API 延迟可接受则降级为实时 API
- 攻略内容：AI 批量预生成 + 人工审核 → 存入本地 JSON
- 增量更新：每周仅更新新增/变动的武将数据

---

## 二、MVP 范围

### 第一阶段：基础建设（本项目第一阶段）

实现以下功能：

1. **数据模型与存储**：武将信息、相性评分、攻略指南的 JSON 定义与读写
2. **数据采集工具**：官网爬虫（武将基础信息，已完成）、AI 批量生成脚本（攻略/相性，待开发）
3. **桌面应用主框架**：PyQt 主窗口 + 英雄浏览器（查看武将详情 + 攻略）
4. **选将推荐（预计算模式）**：
   - 从 MuMu 模拟器截取选将画面
   - 轮廓检测识别选将框中的武将
   - OCR 提取武将名称
   - 查本地相性表 → 展示推荐结果

### 第二阶段（未来迭代，此处仅做预留）

- 实时 API 推荐模式
- 数据管理工具
- 对局中 Overlay 显示
- 牌局卡牌识别

---

## 三、目录结构

```
G:\py_savepoint\test_project\
│
├── AGENTS.md
├── PLANS.md
├── environment.yml              # Conda 环境定义
├── README.md                    # 项目文档
│
├── src/
│   ├── main.py                  # 应用入口
│   │
│   ├── ui/                      # UI 层
│   │   ├── __init__.py
│   │   ├── main_window.py       # 主窗口
│   │   ├── hero_browser.py      # 武将浏览器
│   │   ├── recommendation.py    # 推荐结果展示
│   │   └── settings_dialog.py   # API 配置对话框
│   │
│   ├── capture/                 # 采集层
│   │   ├── __init__.py
│   │   ├── screen.py            # 模拟器屏幕捕获
│   │   ├── detector.py          # 轮廓检测 + 筛选
│   │   └── ocr.py               # OCR 识别
│   │
│   ├── data/                    # 数据层
│   │   ├── __init__.py
│   │   ├── models.py            # Pydantic 数据模型
│   │   └── manager.py           # JSON 读写 + 增量更新
│   │
│   ├── business/                # 业务层
│   │   ├── __init__.py
│   │   ├── recommendation.py    # 推荐引擎
│   │   └── guide.py             # 攻略服务
│   │
│   └── scraper/                 # 数据采集
│       ├── __init__.py
│       ├── official.py          # 官网爬虫
│       └── ai_batch.py          # AI 批量生成攻略
│
├── data/                        # 本地 JSON 数据文件
│   ├── heroes.json              # 武将基础数据
│   ├── synergies.json           # 相性评分
│   ├── guides.json              # 武将攻略
│   └── cards.json               # 基础卡牌数据
│
├── tests/                       # 测试
│   ├── __init__.py
│   ├── test_data/
│   ├── test_business/
│   └── test_capture/
│
└── docs/                        # 文档
    ├── field_mapping.md          # 官网字段映射表
    ├── architecture.md           # 架构文档
    └── prompts/                  # LLM Prompt 定稿
        ├── hero_guide.md        # 武将攻略生成 Prompt
        └── synergy_score.md     # 武将相性评分 Prompt
```

---


## 四、数据模型设计

详见 `src/data/models.py`，核心模型包括：

### Hero（武将）

采用 Pydantic 模型 + 中文 JSON 别名映射，通过 `Field(validation_alias=...)` 实现。

```python
class Skill(BaseModel):
    """技能"""
    name: str = Field(..., description="技能名称")
    description: str = Field(default="", description="技能描述")
    settlement: str = Field(default="", description="结算详情")


class Hero(BaseModel):
    """武将"""
    id: int = Field(validation_alias="角色ID")
    name: str = Field(validation_alias="名称")
    faction: str = Field(validation_alias="势力")
    position: str = Field(validation_alias="定位")       # 输出 / 辅助 / 控制 / 防御
    max_hp: int = Field(validation_alias="体力上限")
    max_hand: int = Field(validation_alias="手牌上限")
    gender: str = Field(validation_alias="性别")          # 男 / 女
    skills: list[Skill] = Field(validation_alias="技能")
```

### SynergyScore（相性评分）

```python
class SynergyScore(BaseModel):
    """武将相性评分"""
    hero_a_id: int                # 武将A ID
    hero_b_id: int                # 武将B ID
    score: int                    # -10 ~ 10，正为配合好，负为克制
    synergy_rating: str           # S/A/B/C/D 总评
    combo_ceiling: int            # 配合上限 1-10
    combo_stability: int          # 配合稳定性 1-10
    adaptability: int             # 环境适应力 1-10
    description: str              # 相性总评的一句话定性判断
```

### HeroGuide（攻略）

```python
class HeroGuide(BaseModel):
    """武将攻略"""
    hero_id: int
    key_points: list[str]        # 操作要点
    counters: list[int]          # 被谁克制（英雄ID列表）
    synergizes_with: list[int]   # 与谁搭配好
    description: str             # 攻略正文
    tips_for_beginners: str      # 新手提示
```

### Card（基础卡牌）

```python
class Card(BaseModel):
    """对局内基础卡牌"""
    id: str = Field(validation_alias="id")
    name: str = Field(validation_alias="name")
    card_type: str = Field(validation_alias="card_type")       # 行动牌 / 战法牌 / 装备牌 等
    card_desc: str = Field(validation_alias="card_desc")       # 简短描述
    card_detail: str = Field(validation_alias="card_detail")   # 规则详解
    card_amount: int = Field(validation_alias="card_amount")   # 牌堆中数量
```

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
| 2.1 | 官网结构探查 | 分析官网 HTML 结构 → docs/field_mapping.md | ✅ 已完成 |
| 2.2 | 爬虫实现 + 数据清洗 | official.py（爬取→解析→HTML清洗→字段映射→Pydantic校验） | ✅ 已完成 |
| 2.3 | 攻略生成 Prompt 设计与实现 | docs/prompts/hero_guide.md | ✅ 已完成 |
| 2.4 | 相性评分 Prompt 设计 | docs/prompts/synergy_score.md | ✅ 已完成 |
| 2.5 | 批量生成脚本 | ai_batch.py（调用 DeepSeek API + 断点续传 + Pydantic 校验输出，支持 config.env 配置） | ✅ 已完成 |

### 数据清洗说明（2.1 → 2.2）

官网爬取的数据经常出现多个维度的信息挤在一个字段中（如技能描述字段混入"技能描述+结算详解+技能典故+设计思路"），清洗策略如下：

**探查阶段（2.1）：**
1. 抓取官网 HTML，分析 DOM 结构，确定每个字段对应的 HTML 元素
2. 输出字段映射表，标注哪些字段是纯净的、哪些需要拆分

**清洗阶段（2.2）：**
1. 对已知的拼接字段（如技能描述），用正则/分隔符拆分
2. 拆分后的子字段逐一映射到 Pydantic 模型对应字段
3. 对无法自动拆分的条目标记为"需人工审核"，写入异常日志
4. 最终输出通过 Pydantic schema 校验后才能写入 JSON

**优先级：==先确保结构正确，再考虑数据完整==**，宁可字段为空也不要把脏数据塞进模型。

### AI 批量生成配置说明（2.5）

批量生成脚本 `src/scraper/ai_batch.py` 默认使用 **DeepSeek Chat** 模型，通过 DeepSeek 开放平台 API 调用。

**配置方式（优先级从高到低）：**

| 优先级 | 方式 | 示例 |
|--------|------|------|
| 1（最高） | config.env 配置文件 | DEEPSEEK_API_KEY=sk-xxx |
| 2 | DEEPSEEK_API_KEY / OPENAI_API_KEY 环境变量 | ${env:DEEPSEEK_API_KEY}="sk-xxx" |
| 3（最低） | 内置默认值 | --- |

> **推荐方式**：在项目根目录的 config.env 中配置 API Key（KEY=VALUE 格式），避免每次输入。
> 所有运行时参数（速率限制、超时、重试次数）也支持通过 config.env 配置。
>
> config.env 完整结构：
> `json
> {
>   "ai": {
>     "api_key": "sk-xxx",
>     "api_url": "https://api.deepseek.com/v1/chat/completions",
>     "model": "deepseek-v4-pro",
>     "requests_per_minute": 30,
>     "http_timeout": 300,
>     "max_retries": 3
>   }
> }
> `


**可选参数：**
- `--api-url`：自定义 API 端点（默认 `https://api.deepseek.com/v1/chat/completions`）
- `--model`：模型名称（默认 `deepseek-v4-pro`）
- `--score-threshold`：相性评分过滤下限（默认 0，仅保存 >= 此值的相性）
- `--dry-run`：预估 Token 消耗和费用，不实际调用 API

**成本参考（149 武将，deepseek-v4-pro）：**
- 攻略生成：~223K tokens，约 **CNY 0.98**
- 全量相性（11,026 对）：~8.8M tokens，约 **CNY 33.08**
- 建议使用 `--score-threshold` 过滤低分相性以减少 Token 消耗
- 定价：输入 CNY3/百万tokens，输出 CNY6/百万tokens（缓存未命中）

### 阶段三：桌面应用 UI

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| 3.1 | 主窗口框架 | main_window.py | ✅ 已完成 |
| 3.2 | 武将浏览器 | hero_browser.py（列表 + 详情 + 攻略） | ✅ 已完成 |
| 3.3 | API 配置对话框 | settings_dialog.py | ✅ 已完成 |
| 3.4 | 应用入口集成 | main.py | ✅ 已完成 |
| 3.5 | 菜单栏——武将获取子菜单 | main_window.py（新增 `_fetch_all_heroes`） | ✅ 已完成 |

### 阶段四：屏幕采集与识别

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| 4.1 | MuMu 模拟器截图 | screen.py | ⏳ 待开发 |
| 4.2 | 轮廓检测选将框 | detector.py | ⏳ 待开发 |
| 4.3 | OCR 识别武将名 | ocr.py | ⏳ 待开发 |

### 阶段五：推荐引擎

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| 5.1 | 相性查询引擎 | recommendation.py（选将推荐） | ⏳ 待开发 |
| 5.2 | 推荐结果展示集成 | 推荐面板绑定到 UI | ⏳ 待开发 |

### 阶段六：文档与收尾

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| 6.1 | 架构文档 | docs/architecture.md | ⏳ 待开发 |
| 6.2 | 使用文档 | README.md | ⏳ 待开发 |
| 6.3 | 增量更新流程说明 | 数据更新操作手册 | ⏳ 待开发 |

---

## 六、风险分析

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| MuMu 截屏延迟 | 体验下降 | 使用 ADB 截图，延迟<100ms |
| OCR 识别武将名不准 | 推荐失败 | 结合轮廓位置 + 名称列表过滤 |
| 新武将每周更新，需持续维护 | 维护成本 | 增量更新机制 + 定时提醒 |
| API 实时推荐延迟超时 | 功能不可用 | 降级为预计算模式 |
| 游戏UI改版 | 采集失效 | 轮廓检测参数配置化，易适配 |
| 150 武将全量相性组合多 | 生成成本高 | 设定分数下限过滤，仅保留有效组合 |

---

## 七、开发原则（执行中遵循）

1. **先写测试**：数据模型和业务层先写单元测试
2. **小步迭代**：每个阶段完成后保持可运行状态
3. **配置化坐标**：所有屏幕坐标在采集层配置化，禁止硬编码
4. **类型注解**：所有函数使用 Python typing 类型标注
5. **日志完整**：各层均使用 logging 记录关键操作
6. **遵守 AGENTS.md**：遵循根目录 AGENTS.md 中的规范








