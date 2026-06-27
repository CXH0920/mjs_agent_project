# 名将杀 Agent

名将杀桌面辅助工具，面向[名将杀手游](https://mjs.ztgame.com/)的轻度玩家，运行于 PC 端。
提供**选将推荐**、**武将数据库查询**、**AI 批量攻略生成**和**武将相性分析**功能。

---

## 目录结构

```
test_project/
├── src/
│   ├── main.py                    # 应用入口
│   ├── config/
│   │   └── env.py                 # .env 解析/加载/保存
│   ├── data/
│   │   ├── models.py              # Pydantic 数据模型（Hero/SynergyScore/HeroGuide/...）
│   │   ├── manager.py             # DataFacade 门面 + 增量更新函数
│   │   ├── hero_manager.py        # 武将 CRUD + JSON 持久化
│   │   ├── synergy_manager.py     # 相性评分 CRUD + JSON 持久化
│   │   └── guide_manager.py       # 攻略 CRUD + JSON 持久化
│   ├── scraper/
│   │   ├── crawler.py             # 爬虫核心（公开 API，含头像下载）
│   │   ├── official.py            # 官网全量爬虫（支持 --skip-images）
│   │   ├── incremental.py         # 增量/指定爬虫（支持 --skip-images）
│   │   ├── ai_utils.py            # AI 生成共享工具函数
│   │   ├── ai_generator.py        # AIBatchGenerator（API 调用/限速/Prompt/校验）
│   │   ├── ai_playwright.py       # PlaywrightGenerator（浏览器自动化模式）
│   │   ├── ai_batch.py            # CLI 入口（纯编排，不含业务逻辑）
│   │   ├── ai_guide.py            # 攻略生成循环
│   │   ├── ai_synergy.py          # 全量相性评分生成循环
│   │   ├── ai_synergy_pair.py     # 指定两武将相性配对生成
│   │   └── ai_synergy_single.py   # 选定武将 x 全体相性生成
│   ├── business/
│   │   ├── fetch_service.py       # 武将采集业务（QProcess 管理）
│   │   ├── guide_fetch_service.py # 攻略生成业务（QProcess 管理）
│   │   └── synergy_fetch_service.py # 相性获取业务（QProcess 管理）
│   ├── capture/
│   │   └── __init__.py            # 屏幕采集（待开发）
│   └── ui/
│       ├── style.py               # 全局样式表（天蓝色调）
│       ├── main_window.py         # 主窗口（菜单栏/Tab/状态栏）
│       ├── hero_browser.py        # 武将浏览（列表+详情+攻略）
│       ├── hero_select_dialog.py  # 武将选择对话框基类
│       ├── recommendation_panel.py # 选将推荐面板（4×2 网格+头像+相性）
│       ├── backend_choose_dialog.py # 后端选择（API/浏览器双 Tab）
│       ├── fetch_dialog.py        # 武将获取选择
│       ├── guide_fetch_dialog.py  # 攻略获取选择
│       ├── synergy_pair_dialog.py # 相性指定获取（选 2 武将）
│       ├── synergy_single_dialog.py # 相性选定武将（选 1 武将）
│       ├── settings_dialog.py     # API 配置对话框
│       ├── cost_confirm_dialog.py # AI 成本确认对话框（API 模式）
│       └── guide_progress_dialog.py # 攻略生成进度条
├── data/
│   ├── heroes.json                # 155 个武将
│   ├── synergies.json             # 相性评分
│   ├── guides.json                # 武将攻略
│   ├── cards.json                 # 基础卡牌数据
│   └── 2v2胜率排行.csv            # 2v2 胜率数据
├── images/
│   └── <武将名>.png               # 155 个武将头像（从官网自动下载）
├── tests/
│   ├── test_models.py             # 25 tests — 数据模型校验
│   ├── test_ai_batch.py           # 33 tests — AI 批量生成
│   ├── test_hero_manager.py       # 13 tests — 武将管理器
│   ├── test_synergy_manager.py    # 13 tests — 相性管理器
│   ├── test_guide_manager.py      # 11 tests — 攻略管理器
│   ├── test_incremental_update.py # 8 tests — 增量更新
│   └── test_ui.py                 # 4 tests — UI 工具
├── docs/
│   ├── field_mapping.md           # 官网字段映射说明
│   └── prompts/
│       ├── hero_guide.md          # 攻略生成 Prompt
│       └── synergy_score.md       # 相性评分 Prompt
├── project_problem.md             # 项目问题记录文档
├── project_doc.md                 # 项目细节文档
├── AGENTS.md                      # 开发规范
├── PLANS.md                       # 实施方案与阶段
├── CLAUDE.md                      # Claude Code 上下文文件
├── environment.yml                # Conda 环境定义
└── README.md                      # 本文件
```

---

## 快速开始

### 1. 环境准备

```bash
conda env create -f environment.yml
conda activate myenv

# 浏览器模式需额外安装 Playwright
pip install playwright
playwright install msedge
```

### 2. 运行测试

```bash
pytest tests/ -v
```

预期输出：**112 passed**（数据模型 + AI 批量生成 + 三个 Manager + 增量更新 + UI 工具）

### 3. 启动桌面应用

```bash
python -m src.main
```

### 4. 数据采集

```bash
# 官网全量采集（自动下载头像到 images/）
python -m src.scraper.official

# 跳过头像下载
python -m src.scraper.official --skip-images

# 增量采集（仅爬取本地没有的武将，并下载新武将头像）
python -m src.scraper.incremental --incremental

# 指定武将
python -m src.scraper.incremental --hero 诸葛亮,关羽
python -m src.scraper.incremental --hero-id 52,114
```

### 5. AI 批量生成

```bash
# API 模式（需配置 DEEPSEEK_API_KEY）
python -m src.scraper.ai_batch --guide                      # 生成攻略
python -m src.scraper.ai_batch --synergy                     # 全量相性

# 浏览器模式（免费，需手动登录 DeepSeek 网页版）
python -m src.scraper.ai_batch --guide --browser             # 攻略
python -m src.scraper.ai_batch --synergy --browser           # 相性

# 预览成本（仅 API 模式）
python -m src.scraper.ai_batch --dry-run --guide
python -m src.scraper.ai_batch --dry-run --synergy
```

---

## 架构

### 四层架构

```
UI 层 (PySide6)      主窗口 / 武将浏览器 / 选将推荐 / 各对话框
业务层 (Business)    采集/攻略/相性 QProcess 管理
数据层 (Data)        Pydantic 模型 + DataFacade + JSON 持久化
采集层 (Scraper)     官网爬虫 + AI 批量生成 + 头像下载（屏幕采集待开发）
```

**跨层模块：**
- **配置层** — `src/config/env.py` 统一管理 API 配置和运行时参数
- **爬虫层** — `src/scraper/` 实现官网数据采集、AI 批量生成和头像下载

### 数据流

```
官网页面 → crawler.py 解析 JS chunk → official.py/incremental.py 清洗校验 → data/heroes.json
                                                                        ↘ images/<武将名>.png
DeepSeek API / 网页版 → ai_batch.py → ai_generator.py / ai_playwright.py
  → ai_guide/ai_synergy/… → data/{guides,synergies}.json
data/*.json → DataFacade (三个 Manager) → UI 展示
用户操作 → MainWindow → QProcess → 爬虫/AI 脚本
```

### 双模式 AI 生成

```
AI 生成
 ├── API 模式 (默认)    → AIBatchGenerator → httpx → DeepSeek API
 └── 浏览器模式 (--browser) → PlaywrightGenerator → Playwright + Edge → chat.deepseek.com
```

- **API 模式**：速度快、支持 Token 统计和费用估算、需要付费 API Key
- **浏览器模式**：免费、无需 API Key、速度较慢（需等待浏览器）、不支持 Token 统计
- 生成的效果和 JSON 输出格式一致，差异仅在后端传输方式

### 配置加载优先级

```
config.env > 环境变量 > 默认值
```

---

## 数据模型

核心模型定义在 `src/data/models.py`（Pydantic v2），支持中文 `validation_alias` 映射官网字段：

| 模型 | 说明 | 关键字段 |
|------|------|----------|
| Hero | 武将基础数据 | id, name, title, faction, position, max_hp, max_hand, gender, skills, difficulty, mode_viability, icon_url |
| Skill | 武将技能 | name, description, settlement |
| SynergyScore | 武将间相性评分 | hero_a_id, hero_b_id, score(-10~10), synergy_rating(S/A/B/C/D), combo_ceiling/stability/adaptability |
| HeroGuide | 武将攻略 | hero_id, key_points, counters, synergizes_with, description, tips_for_beginners |
| Card | 对局内基础卡牌 | id, name, card_type, card_desc, card_detail, card_amount |
| IncrementalUpdate | 增量更新结构 | added/modified/removed 三类变更 |

---

## 数据管理器

通过 `DataFacade` 统一访问三个 Manager：

```python
facade = DataFacade()
facade.load_all()            # 一次性加载所有数据
stats = facade.get_stats()   # {heroes: N, synergies: N, guides: N}
facade.heroes.get_hero(114)  # 直接访问各 Manager
facade.heroes.search_heroes("诸葛")  # 模糊搜索
```

各 Manager 功能特性：
- **HeroManager** — 武将 CRUD，支持 ID/名称/关键词/势力查询
- **SynergyManager** — 相性 CRUD，(A,B) 和 (B,A) 自动归一为同一 key
- **GuideManager** — 攻略 CRUD，以 hero_id 为 key

---

## 爬虫模块

### 爬虫核心 (`src/scraper/crawler.py`)

提供 `fetch(binary=True)` / `find_chunk_url()` / `extract_js_array()` / `js_to_json()` / `transform()` / `validate_heroes()` 等公开 API。核心逻辑：

1. 请求官网页面，定位 JS chunk URL
2. 下载 JS chunk，提取 `const e=[...]` 数组
3. JS 语法 → JSON 解析
4. 字段映射（性别数字→枚举、HTML→纯文本拆分技能描述/结算、icon_url 提取）
5. Pydantic 校验

此外，`download_hero_images()` 从原始 JS 数据中提取每个武将的 `icon_url`，下载到 `images/{武将名}.png`。

### 官网全量爬虫 (`src/scraper/official.py`)

5 步清洗流程 + 头像下载：定位数据源 → 下载 JS → 解析 → 清洗映射 → Pydantic 校验 + JSON 输出 + 头像下载。

`--skip-images` 跳过头像下载。

### 增量爬虫 (`src/scraper/incremental.py`)

三种模式 + 头像下载：
- `--incremental` — 仅追加本地没有的新武将
- `--hero` — 按名称采集（支持模糊匹配）
- `--hero-id` — 按 ID 采集

`--skip-images` 跳过头像下载。

---

## AI 批量生成

```
src/scraper/
├── ai_batch.py          CLI 入口（参数解析 → 配置加载 → 委托子模块）
├── ai_generator.py      API 调用核心（限速/重试/JSON 提取/Pydantic 校验）
├── ai_playwright.py     浏览器自动化生成器（Playwright + Edge）
├── ai_guide.py          逐个武将生成攻略
├── ai_synergy.py        全量相性生成（所有武将两两配对）
├── ai_synergy_pair.py   指定两武将配对生成
├── ai_synergy_single.py 选定武将 vs 全体生成
└── ai_utils.py          共享工具（estimate_cost / load_heroes / _save_json）
```

**特性：**
- 各模块单向调用，无循环导入
- 支持断点续传（跳过已有项）
- 输出经过 Pydantic 模型校验
- `--dry-run` 预览 Token 消耗和费用（仅 API 模式）
- 批量保存中间结果，中断不丢数据
- 双模式：API 直连 / 浏览器自动化（`--browser`）

### ETL 数据流

```
AI 回复（含分析正文 + --- 分隔线 + ```json 代码块）
  │ 1. Extract
  ▼
原始回复文本(str)
  │ 2. Transform
  ▼
_extract_json() → raw_decode 宽容解析 + 状态机修复字面换行 → Python dict
_convert_ids_to_int() → 武将 ID 转 int → 注入 hero_id / hero_a_id / hero_b_id
  │ 3. Load
  ▼
_validate_guide() / _validate_synergy() → Pydantic 校验 → model_dump
  ▼
_save_json() → data/guides.json / data/synergies.json
```

---

## 桌面应用功能

### 主窗口

| 菜单 | 功能 | 说明 |
|---|---|---|
| 文件 > 退出 | Ctrl+Q | 关闭应用 |
| 配置 > API 配置 | | 编辑 config.env |
| 数据 > 重新加载数据 | F5 | 重新读取 JSON 文件 |
| 数据 > 武将获取 > 全量/增量/指定 | | 从官网采集武将（含头像下载） |
| 数据 > 攻略获取 > 全量/增量/指定 | | AI 批量生成攻略 → BackendChooseDialog（API/浏览器） |
| 数据 > 武将相性 > 选定武将 | | 选 1 武将，计算其与全体其他武将的相性 → BackendChooseDialog |
| 数据 > 武将相性 > 指定获取 | | 选 2 武将，计算这对的相性评分 → BackendChooseDialog |
| 帮助 > 关于 | | 版本信息 |

所有耗时操作均通过 **QProcess** 异步执行，不阻塞 UI。

### 后端选择对话框 (`BackendChooseDialog`)

所有攻略/相性操作在确认执行前弹出双 Tab 对话框：

```
┌──────────────────────────────────────────────┐
│  Tab1: API 方式  │  Tab2: 浏览器方式        │
├──────────────────────────────────────────────┤
│  - 成本估算信息    │  浏览器模式说明           │
│  - Token/费用     │  免费无需 API Key        │
│  [确定执行] [取消]  │  需登录 DeepSeek 网页版  │
└──────────────────────────────────────────────┘
```

### 选将推荐

- **4×2 网格布局**，每格一个武将推荐卡片
- 左侧展示**武将头像**（从 `images/` 读取），名称半透明浮在底部，左上角势力色块
- 右侧展示**推荐指数**（星级+置信度百分比）、**高相性组合**、**胜率**（占位）
- 对外提供 `update_recommendations(data: list[dict])` 接口，接收 `{index, name, confidence}` 格式数据
- 默认加载前 8 个武将作为演示

### 武将浏览器

- 左侧列表：支持**搜索过滤** + **势力筛选**
- 右侧详情：Tab 切换「武将信息」和「攻略指南」
- 技能展示：描述 + 可折叠的结算详情
- 攻略展示：Markdown 渲染（mistune） + 克制/搭配关系

### Configuration

`config.env` 文件管理全部 API 配置（已 gitignored）：

```env
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_API_URL=https://api.deepseek.com/v1/chat/completions
DEEPSEEK_MODEL=deepseek-v4-pro
REQUESTS_PER_MINUTE=30
HTTP_TIMEOUT=300
MAX_RETRIES=3
```

定价：输入 **CNY 3/百万 tokens**，输出 **CNY 6/百万 tokens**（deepseek-v4-pro，缓存未命中）

---

## 外部依赖

| 依赖 | 用途 |
|------|------|
| PySide6 | 桌面 UI 框架 |
| pydantic | 数据模型与校验 |
| httpx | DeepSeek API 请求（API 模式） |
| playwright | 浏览器自动化（浏览器模式） |
| beautifulsoup4 | HTML 解析（备用） |
| mistune | Markdown → HTML 渲染 |
| opencv-python / easyocr / mss | 屏幕采集层（待开发） |
| pytest | 测试框架 |

---

## 开发状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| 一 | 项目脚手架与数据模型 | ✅ 已完成 |
| 二 | 数据采集（官网爬虫 + AI 批量生成） | ✅ 已完成 |
| 三 | PySide6 桌面应用 UI | ✅ 已完成 |
| 四 | 武将相性交互获取 | ✅ 已完成 |
| 五 | 武将头像下载（icon_url → images/） | ✅ 已完成 |
| 六 | 选将推荐（4×2 网格+头像+数据接口） | ✅ 已完成 |
| 七 | 浏览器自动化（Playwright + Edge）双模式 AI 生成 | ✅ 已完成 |
| 八 | 屏幕采集（MuMu 截图、轮廓检测、OCR） | ⏳ 待开发 |
| 九 | 推荐引擎（相性查询、推荐数据源接入） | ⏳ 待开发 |

---

## 常见操作命令速查

```bash
# 启动应用
python -m src.main

# 运行所有测试
pytest tests/ -v

# 运行单个测试文件
pytest tests/test_models.py -v

# 官网全量采集（含头像下载）
python -m src.scraper.official [--skip-images]

# 增量采集
python -m src.scraper.incremental --incremental [--skip-images]

# 指定武将采集
python -m src.scraper.incremental --hero 诸葛亮,关羽
python -m src.scraper.incremental --hero-id 52,114

# AI 攻略生成（API 模式）
python -m src.scraper.ai_batch --guide [--dry-run] [--heroes-file path]

# AI 攻略生成（浏览器模式）
python -m src.scraper.ai_batch --guide --browser

# AI 相性评分（API 模式）
python -m src.scraper.ai_batch --synergy [--dry-run] [--score-threshold 0]

# AI 相性评分（浏览器模式）
python -m src.scraper.ai_batch --synergy --browser
```
