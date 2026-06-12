# 名将杀 Agent

名将杀桌面辅助工具，面向名将杀手游的轻度玩家，运行于 PC 端 MuMu 模拟器。

核心功能：**选将推荐** + **武将数据库查询**。

> 开发中，当前完成阶段：阶段一（基础建设）+ 阶段二（数据采集）+ 阶段三（桌面应用 UI）+ 阶段四（相性交互获取）。

---

## 目录结构

```
test_project/
│
├── AGENTS.md              # 开发规范
├── PLANS.md               # 实施计划
├── CLAUDE.md              # Claude Code 上下文文件
├── environment.yml        # Conda 环境定义
├── README.md              # 本文件
│
├── src/                   # 源代码
│   ├── main.py            # 应用入口（全局样式/字体抑制/UTF-8 编码）
│   ├── config/
│   │   └── env.py         # .env 文件解析/加载/保存
│   ├── data/
│   │   ├── models.py          # Pydantic 数据模型
│   │   ├── manager.py         # DataFacade 门面 + 增量更新函数
│   │   ├── hero_manager.py    # 武将数据管理器
│   │   ├── synergy_manager.py # 相性评分数据管理器
│   │   └── guide_manager.py   # 攻略数据管理器
│   ├── scraper/
│   │   ├── crawler.py         # 爬虫核心（公开 API）
│   │   ├── official.py        # 官网全量爬虫
│   │   ├── incremental.py     # 增量/指定爬虫
│   │   ├── ai_utils.py        # AI 生成共享工具函数
│   │   ├── ai_generator.py    # AIBatchGenerator（API 调用/限速/Prompt 构建/校验）
│   │   ├── ai_batch.py        # AI 批量生成 CLI 入口（纯编排）
│   │   ├── ai_guide.py        # 攻略生成循环
│   │   ├── ai_synergy.py      # 全量相性评分生成循环
│   │   ├── ai_synergy_pair.py # 指定两武将相性配对生成
│   │   └── ai_synergy_single.py # 选定武将 x 全体相性生成
│   ├── business/
│   │   ├── fetch_service.py       # 武将采集业务（QProcess）
│   │   ├── guide_fetch_service.py # 攻略生成业务（QProcess）
│   │   └── synergy_fetch_service.py # 相性获取业务（QProcess）
│   ├── capture/              # 采集层（待开发）
│   │   └── __init__.py
│   └── ui/
│       ├── style.py              # 全局样式表（天蓝色调）
│       ├── main_window.py        # 主窗口
│       ├── hero_browser.py       # 武将浏览
│       ├── settings_dialog.py    # API 配置对话框
│       ├── fetch_dialog.py       # 武将获取选择
│       ├── guide_fetch_dialog.py # 攻略获取选择
│       ├── synergy_pair_dialog.py # 相性指定获取（选 2 武将）
│       ├── synergy_single_dialog.py # 相性选定武将（选 1 武将）
│       ├── cost_confirm_dialog.py   # 成本确认
│       └── guide_progress_dialog.py # 进度条
│
├── data/                  # 本地 JSON 数据
│   ├── heroes.json        # 149 个武将
│   ├── synergies.json     # 相性评分
│   ├── guides.json        # 武将攻略
│   └── cards.json         # 卡牌数据
│
├── tests/
│   ├── test_models.py             # 25 个用例
│   ├── test_ai_batch.py           # 33 个用例
│   ├── test_hero_manager.py       # 13 个用例
│   ├── test_synergy_manager.py    # 13 个用例
│   ├── test_guide_manager.py      # 11 个用例
│   ├── test_incremental_update.py # 8 个用例
│   └── test_ui.py                 # 4 个用例
│
└── docs/
    ├── field_mapping.md      # 官网字段映射说明
    └── prompts/
        ├── hero_guide.md     # 攻略生成 Prompt
        └── synergy_score.md  # 相性评分 Prompt
```

---

## 配置管理

### config.env 配置文件

项目根目录的 `config.env` 用于集中管理 API 配置和运行时参数：

```env
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_API_URL=https://api.deepseek.com/v1/chat/completions
DEEPSEEK_MODEL=deepseek-v4-pro
REQUESTS_PER_MINUTE=30
HTTP_TIMEOUT=300
MAX_RETRIES=3
```

### 配置模块（`src/config/`）

`src/config/env.py` 提供统一的配置加载/保存接口：

- `parse_env_file()` — 解析 .env 文件
- `get_api_config()` — 获取 API 配置（config.env > 环境变量 > 默认值）
- `get_runtime_params()` — 获取运行时参数（速率限制、超时、重试次数）
- `save_env_file()` — 原子写入 .env 文件

---

## 快速开始

### 1. 环境准备

```bash
conda env create -f environment.yml
conda activate myenv
```

### 2. 运行单元测试

```bash
pytest tests/ -v
```

预期输出：102 passed（25 数据模型 + 33 AI 批量生成 + 13 HeroManager + 13 SynergyManager + 11 GuideManager + 8 增量更新 + 4 UI 工具）

### 3. 启动桌面应用

```bash
python -m src.main
```

### 4. 数据采集

```bash
# 官网全量采集
python -m src.scraper.official

# 增量采集
python -m src.scraper.incremental --incremental

# 指定武将
python -m src.scraper.incremental --hero 诸葛亮
```

### 5. AI 批量生成

```bash
# 预览成本
python -m src.scraper.ai_batch --dry-run --guide

# 生成攻略
python -m src.scraper.ai_batch --guide

# 全量相性评分
python -m src.scraper.ai_batch --synergy
```

---

## 架构

### 四层架构

```
UI 层 (PySide6)     → main_window.py, hero_browser.py, 各对话框
业务层 (Business)   → fetch_service.py, guide_fetch_service.py, synergy_fetch_service.py (QProcess)
数据层 (Data)       → models.py (Pydantic) + DataFacade (HeroManager / SynergyManager / GuideManager)
采集层 (Capture)    → 待开发 (screen.py, detector.py, ocr.py)
```

跨层模块：
- **配置层** — `src/config/env.py` 提供统一配置管理
- **爬虫层** — `src/scraper/` 实现官网数据采集和 AI 批量生成

### 数据流

```
官网页面 → crawler.py 解析 JS chunk → official.py/incremental.py 清洗校验 → data/heroes.json
DeepSeek API → ai_batch.py CLI → ai_generator.py → ai_guide.py / ai_synergy.py / ai_synergy_pair.py / ai_synergy_single.py → data/guides.json + data/synergies.json
JSON 文件 → DataFacade → UI 展示
用户操作 → MainWindow → QProcess → 爬虫脚本
```

### 配置加载优先级

```
config.env > 环境变量 > 默认值
```

---

## AI 批量生成模块层次

```
src/scraper/
├── ai_batch.py          CLI 入口（参数解析 → 配置加载 → 委托子模块）
├── ai_generator.py      API 调用核心（重试/限速/JSON 提取/Pydantic 校验）
├── ai_guide.py          攻略生成循环
├── ai_synergy.py        全量相性生成循环（所有武将两两配对）
├── ai_synergy_pair.py   指定两武将相性配对生成（UI 指定获取→exec）
├── ai_synergy_single.py 选定武将 x 全体相性生成（UI 选定武将→exec）
└── ai_utils.py          共享工具（estimate_cost / load_heroes / _save_json）
```

**特性：**
- 各模块通过函数参数接收依赖，无循环导入
- 支持断点续传（跳过已生成的数据）
- 输出经过 Pydantic 模型校验
- `--dry-run` 预览 Token 消耗和费用

---

## 数据模型

核心模型定义在 `src/data/models.py`（Pydantic v2）：

| 模型 | 说明 | 关键字段 |
|------|------|----------|
| Hero | 武将基础数据 | id, name, title, faction, position, gender, max_hp, max_hand, skills, difficulty, mode_viability |
| Skill | 武将技能 | name, description, settlement |
| SynergyScore | 武将间相性评分 | hero_a_id, hero_b_id, score(-10~10), synergy_rating(S/A/B/C/D) |
| HeroGuide | 武将攻略 | hero_id, key_points, counters, synergizes_with, description, tips_for_beginners |
| Card | 对局内基础卡牌 | id, name, card_type, card_desc, card_detail, card_amount |
| IncrementalUpdate | 增量更新结构 | added/modified/removed 三类变更 |

---

## 数据管理器

数据层通过 `DataFacade` 统一访问：

```python
facade = DataFacade()
facade.load_all()            # 一次性加载所有数据
stats = facade.get_stats()   # {heroes: N, synergies: N, guides: N}
facade.heroes.get_hero(114)  # 直接访问各 Manager
```

### HeroManager (src/data/hero_manager.py)

武将 CRUD + JSON 持久化。支持按 ID/名称查询、关键词搜索、势力筛选。

### SynergyManager (src/data/synergy_manager.py)

相性评分 CRUD + JSON 持久化。(A,B) 和 (B,A) 自动映射为同一 key。

### GuideManager (src/data/guide_manager.py)

攻略 CRUD + JSON 持久化，以 hero_id 为 key。

---

## 爬虫模块

### 爬虫核心 (src/scraper/crawler.py)

公共 API：`fetch()` / `find_chunk_url()` / `extract_js_array()` / `js_to_json()` / `transform()` / `validate_heroes()` / `fetch_all_raw()`

### 官网全量爬虫 (src/scraper/official.py)

5 步流程：定位数据源 → 下载 JS → 解析 → 清洗映射 → Pydantic 校验 + JSON 输出。

### 增量爬虫 (src/scraper/incremental.py)

三种模式：增量（仅追加本地没有的）、按名称采集、按 ID 采集。

---

## 菜单栏功能

| 菜单 | 功能 | 说明 |
|---|---|---|
| 文件 > 退出 | Ctrl+Q | 关闭应用 |
| 工具 > API 配置 | | 编辑 config.env |
| 数据 > 重新加载数据 | F5 | 重新读取 JSON 数据 |
| 数据 > 武将获取 > 全量/增量/指定 | | 从官网采集武将 |
| 数据 > 攻略获取 > 全量/增量/指定 | | AI 批量生成攻略（成本确认+进度条） |
| **数据 > 武将相性 > 选定武将** | | **选 1 武将，计算其与全体武将的相性** |
| **数据 > 武将相性 > 指定获取** | | **选 2 武将，计算这对的相性评分** |
| 帮助 > 关于 | | 版本信息 |

> 武将相性采用 QProcess 异步执行，不阻塞 UI。

---

## 可用命令

```bash
# 启动应用
python -m src.main

# 运行测试
pytest tests/ -v
pytest tests/test_models.py -v

# 官网全量采集
python -m src.scraper.official [--dry-run] [--output path] [--verbose]

# 增量采集
python -m src.scraper.incremental --incremental [--dry-run] [--output path]

# 指定武将采集
python -m src.scraper.incremental --hero 诸葛亮,关羽
python -m src.scraper.incremental --hero-id 52,114

# AI 攻略生成
python -m src.scraper.ai_batch --guide [--dry-run] [--heroes-file path]

# AI 全量相性生成
python -m src.scraper.ai_batch --synergy [--dry-run] [--score-threshold 0]
```

---

## 外部依赖

- **PySide6** — 桌面 UI
- **Pydantic** — 数据模型校验
- **httpx** — AI API 请求
- **beautifulsoup4** — HTML 解析（备用）
- **opencv-python / easyocr / mss** — 待开发阶段（截图/OCR）
- **pytest** — 测试框架

---

## API 配置

在 `config.env` 中配置（已 gitignored）：

```env
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_API_URL=https://api.deepseek.com/v1/chat/completions
DEEPSEEK_MODEL=deepseek-v4-pro
REQUESTS_PER_MINUTE=30
HTTP_TIMEOUT=300
MAX_RETRIES=3
```

定价：输入 CNY 3/百万 tokens，输出 CNY 6/百万 tokens（deepseek-v4-pro 缓存未命中）。

---

## 阶段状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| 一 | 项目脚手架与数据模型 | ✅ 已完成 |
| 二 | 数据采集工具（官网爬虫 + AI 批量生成） | ✅ 已完成 |
| 三 | PySide6 桌面应用 UI | ✅ 已完成 |
| 四 | 武将相性交互获取（选定武将/指定获取） | ✅ 已完成 |
| 五 | 屏幕采集（MuMu 截图、轮廓检测、OCR） | 待开发 |
| 六 | 推荐引擎（相性查询、推荐展示） | 待开发 |
