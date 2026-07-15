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
│   │   ├── env.py                 # .env 解析/加载/保存
│   │   └── logging_config.py     # 统一日志配置（按模块拆分 + 文件轮转）
│   ├── data/
│   │   ├── models.py              # Pydantic 数据模型（Hero/SynergyScore/HeroGuide/...）
│   │   ├── manager.py             # DataManager[V_co] 泛型基类 + DataFacade 门面 + 增量更新
│   │   ├── hero_manager.py        # 武将 CRUD + JSON 持久化（继承 DataManager[Hero]）
│   │   ├── synergy_manager.py     # 相性评分 CRUD + JSON 持久化（继承 DataManager[SynergyScore]）
│   │   └── guide_manager.py       # 攻略 CRUD + JSON 持久化（继承 DataManager[HeroGuide]）
│   ├── scraper/
│   │   ├── crawler.py             # 爬虫核心（公开 API，含头像下载）
│   │   ├── official.py            # 官网全量爬虫（支持 --skip-images）
│   │   ├── incremental.py         # 增量/指定爬虫（支持 --skip-images）
│   │   ├── ai_utils.py            # AI 生成共享工具函数
│   │   ├── ai_generator.py        # AIBatchGenerator（API 调用/限速/Prompt/校验）
│   │   ├── ai_playwright.py       # PlaywrightGenerator（浏览器自动化模式）
│   │   ├── ai_batch.py            # CLI 入口（纯编排，不含业务逻辑）
│   │   └── ai_generation.py       # 四种生成编排函数（攻略/全量相性/指定配对/选定武将）
│   ├── business/
│   │   ├── base_fetch_service.py  # BaseFetchService 基类（QProcess 通用管理方法）
│   │   ├── fetch_service.py       # 武将采集业务（继承 BaseFetchService）
│   │   ├── guide_fetch_service.py # 攻略生成业务（继承 BaseFetchService）
│   │   ├── synergy_fetch_service.py # 相性获取业务（继承 BaseFetchService）
│   │   ├── capture_service.py     # 截图业务编排（ADB 截图 + OCR 调度）
│   │   ├── ocr_service.py         # OCR 控制服务（模板管理 + 轮询）
│   │   └── fetch_utils.py         # QProcess 公共工具函数
│   ├── capture/
│   │   ├── __init__.py
│   │   ├── adb_screen.py          # ADB 连接与截图（subprocess exec-out 无文件中间态）
│   │   ├── prober.py              # MuMu 设备自动探测（注册表/环境变量/常见路径）
│   │   └── image_utils.py         # PIL ↔ QPixmap / 剪贴板 / 图像保存
│   ├── ocr/
│   │   ├── __init__.py
│   │   ├── template_manager.py    # OpenCV 模板匹配（TM_CCOEFF_NORMED，<50ms）
│   │   ├── recognizer.py          # PaddleOCR + 编辑距离矫正（两段式识别，内存中处理）
│   │   └── ocr_loader.py          # 单例延迟加载
│   └── ui/
│       ├── style.py               # 全局样式表（天蓝色调）
│       ├── main_window.py         # 主窗口（菜单栏/Tab/状态栏 + 轮询编排）
│       ├── hero_browser.py        # 武将浏览（列表+详情+攻略）
│       ├── hero_select_dialog.py  # 武将选择对话框基类
│       ├── recommendation_panel.py # 选将推荐面板（4×2 网格+头像+相性 + 截图导入）
│       ├── backend_choose_dialog.py # 后端选择（API/浏览器双 Tab）
│       ├── mumu_config_dialog.py  # 模拟器配置对话框（ADB 连接 + 模板管理 + OCR 配置）
│       ├── roi_selector.py        # 模板 ROI 框选对话框（拖拽选区 + 坐标缩放）
│       ├── fetch_dialog.py        # 武将获取选择
│       ├── guide_fetch_dialog.py  # 攻略获取选择
│       ├── synergy_pair_dialog.py # 相性指定获取（选 2~8 武将，自动两两配对）
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
├── templates/
│   └── wujiang_select.png         # 武将选择页面模板（用户自行制作）
├── screenshots/                   # 手动截图导出目录
├── screenshot_data/
│   └── latest.json                # OCR 识别结果缓存
├── logs/
│   └── app.log / scraper/ / business/ / subprocess/  # 按模块拆分的日志文件
├── tests/
│   ├── test_models.py             # 25 tests — 数据模型校验
│   ├── test_ai_batch.py           # 33 tests — AI 批量生成
│   ├── test_hero_manager.py       # 13 tests — 武将管理器
│   ├── test_synergy_manager.py    # 13 tests — 相性管理器
│   ├── test_guide_manager.py      # 11 tests — 攻略管理器
│   ├── test_incremental_update.py # 8 tests — 增量更新
│   └── test_ui.py                 # 4 tests — UI 工具
├── docs/
│   ├── code_desc/
│   │   ├── summary.md              # 项目总览（核心功能、技术栈、模块索引）
│   │   ├── module_config.md        # 应用入口与配置模块说明
│   │   ├── module_data.md          # 数据模型与数据管理模块说明
│   │   ├── module_scraper.md       # 爬虫与数据采集模块说明
│   │   ├── module_ai_batch.md      # AI 批量生成模块说明
│   │   ├── module_business.md      # 业务服务层模块说明
│   │   ├── module_capture_ocr.md   # 屏幕采集与 OCR 模块说明
│   │   └── module_ui.md            # UI 界面层模块说明
│   ├── call_graph/
│   │   ├── call_graph_ai_batch.md  # AI 批量生成调用链路
│   │   ├── call_graph_business.md  # 业务服务层调用链路
│   │   ├── call_graph_data.md      # 数据层调用链路
│   │   ├── call_graph_scraper.md   # 爬虫调用链路
│   │   └── call_graph_ui.md        # UI 界面层调用链路
│   ├── spec/                       # 设计规格文档
│   ├── prompts/
│   │   ├── hero_guide.md           # 攻略生成 Prompt
│   │   └── synergy_score.md        # 相性评分 Prompt
│   ├── field_mapping.md            # 官网字段映射说明
│   └── project_doc.md              # 完整项目细节文档
├── project_problem.md             # 项目问题记录文档
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

### 6. 屏幕采集 + OCR 识别

```bash
# 启动桌面应用（模拟器配置在 配置 → 模拟器配置）
python -m src.main
```

屏幕采集模块（src/capture/ + src/ocr/）在应用内以 UI 集成方式使用，无独立 CLI 入口。具体操作：
1. 打开应用 → 配置 → 模拟器配置
2. 自动探测或浏览选择 ADB 路径
3. 连接模拟器 → 制作模板（框选武将选择页特征区域）
4. 启用武将识别 / 持续轮询
5. 在选将推荐面板点击「截图」或「📁 从图片导入」触发识别

---

## 架构

### 四层架构

```
UI 层 (PySide6)      主窗口 / 武将浏览器 / 选将推荐 / 各对话框
业务层 (Business)    采集/攻略/相性 QProcess 管理 + ADB 截图编排 + OCR 轮询
数据层 (Data)        Pydantic 模型 + DataFacade + JSON 持久化
采集层 (Scraper)     官网爬虫 + AI 批量生成 + 头像下载
```

**跨层模块：**
- **配置层** — `src/config/env.py` 统一管理 API/日志/模拟器配置
- **采集层** — `src/capture/` ADB 连接与截图
- **OCR 层** — `src/ocr/` 模板匹配 + PaddleOCR 识别

### 数据流

```
官网页面 → crawler.py 解析 JS chunk → official.py/incremental.py 清洗校验 → data/heroes.json
                                                                        ↘ images/<武将名>.png
DeepSeek API / 网页版 → ai_batch.py → ai_generator.py / ai_playwright.py
  → ai_generation.py → data/{guides,synergies}.json
data/*.json → DataFacade (三个 Manager) → UI 展示

模拟器屏幕 → ADB screencap → PIL Image（全在内存，无磁盘 I/O）
  → TemplateManager.match() → 武将选择页？
      → 否：静默跳过
      → 是：GeneralRecognizer.recognize() → 填充推荐面板 8 槽
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
- **DataManager[V_co]** — 泛型基类（位于 `manager.py`），定义通用的 `get`/`list_all`/`add`/`update`/`delete`/`load`/`save` 方法
- **HeroManager(DataManager[Hero])** — 武将 CRUD，支持 ID/名称/关键词/势力查询
- **SynergyManager(DataManager[SynergyScore])** — 相性 CRUD，(A,B) 和 (B,A) 自动归一为同一 key
- **GuideManager(DataManager[HeroGuide])** — 攻略 CRUD，以 hero_id 为 key

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
├── ai_generation.py     四种生成编排函数（攻略/全量相性/指定配对/选定武将）
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
| 配置 > API 配置 | | 编辑 API Key/URL/Model |
| 配置 > 模拟器配置 | | ADB 连接管理 + 模板制作 + OCR 配置 + 持续轮询 |
| 数据 > 重新加载数据 | F5 | 重新读取 JSON 文件 |
| 数据 > 武将获取 > 全量/增量/指定 | | 从官网采集武将（含头像下载） |
| 数据 > 攻略获取 > 全量/增量/指定 | | AI 批量生成攻略 → BackendChooseDialog（API/浏览器） |
| 数据 > 武将相性 > 选定武将 | | 选 1 武将，计算其与全体其他武将的相性 → BackendChooseDialog |
| 数据 > 武将相性 > 指定获取 | | 选 2~8 武将，自动两两配对计算 C(N,2) 组相性评分 → BackendChooseDialog |
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
- 右侧展示**推荐指数**（星级+置信度百分比）、**高相性组合**、**胜率**（从 `2v2胜率排行.csv` 读取），胜率前三自动标记 🥇🥈🥉 奖牌
- 对外提供 `update_recommendations(data: list[dict])` 接口，接收 `{index, name, confidence}` 格式数据
- 支持两种导入方式：
  - **截图** — 通过 ADB 截取模拟器屏幕 → OpenCV 模板匹配武将选择页 → PaddleOCR 识别 8 个武将名 → 自动填入槽位
  - **从图片导入** — 选择本地游戏截图文件 → PaddleOCR 识别 → 填入槽位
- 轮询模式开启后，定时检测模拟器画面是否为武将选择页，自动识别并填充

### 武将浏览器

- 左侧列表：支持**搜索过滤** + **势力筛选**
- 右侧详情：Tab 切换「武将信息」和「攻略指南」
- 技能展示：描述 + 可折叠的结算详情
- 攻略展示：Markdown 渲染（mistune） + 克制/搭配关系
- Tab 栏右上角：武将信息和攻略指南各有独立的"修改"+"删除"按钮（绿色/红色），点击后弹出 `HeroEditDialog` / `GuideEditDialog` 编辑弹窗
- 编辑保存后自动刷新列表，选中项保持为当前武将

### Configuration

`config.env` 文件管理全部配置（已 gitignored）：

```env
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_API_URL=https://api.deepseek.com/v1/chat/completions
DEEPSEEK_MODEL=deepseek-v4-pro
REQUESTS_PER_MINUTE=30
HTTP_TIMEOUT=300
MAX_RETRIES=3
LOG_LEVEL=INFO
LOG_TO_FILE=true
MUMU_ADB_PATH=D:\模拟器\MuMu Player 12\nx_main\adb.exe
MUMU_ADB_PORT=16448
MUMU_OCR_ENABLED=true
MUMU_OCR_POLL_MODE=false
MUMU_OCR_POLL_INTERVAL=2
MUMU_OCR_MATCH_THRESHOLD=0.8
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
| paddlepaddle / paddleocr | OCR 识别引擎 |
| opencv-python | 模板匹配 + 图像预处理 |
| pillow (PIL) | 图像处理 |
| pytest | 测试框架 |

---

## 日志系统

统一日志配置在 `src/config/logging_config.py`，桌面应用启动时自动初始化。

### 日志文件结构

```
logs/
├── app.log                  # UI 操作、数据加载
├── scraper/
│   ├── scraper.log          # 爬虫日志
│   └── ai_batch.log         # AI 生成日志
├── business/
│   └── business.log         # QProcess 启停
└── subprocess/
    ├── stdout.log           # 子进程标准输出
    └── stderr.log           # 子进程错误输出
```

每个文件最大 10MB，保留 5 个备份。

### 配置控制

```env
LOG_LEVEL=INFO
LOG_TO_FILE=true
```

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
| 八 | 屏幕采集（MuMu ADB 截图 + 模板匹配 + PaddleOCR 识别 + 持续轮询） | ✅ 已完成 |
| 九 | 推荐引擎（相性查询、胜率 CSV、OCR 数据导入） | ✅ 已完成 |
| 十 | 武将编辑与攻略编辑（tab-header 级修改/删除按钮 + 编辑弹窗） | ✅ 已完成 |
| 十一 | 相性配对多武将组合（最多 8 武将 × 两两配对） | ✅ 已完成 |

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
