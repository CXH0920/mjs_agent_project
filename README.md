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
│   │   ├── guide_manager.py       # 攻略 CRUD + JSON 持久化（继承 DataManager[HeroGuide]）
│   │   ├── win_rate_repository.py # 2v2 胜率 CSV 读取与默认路径缓存
│   │   └── recommendation_index_repository.py # 推荐指数计算与 CSV 快照输出
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
│   │   ├── emulator_operation_service.py # 模拟器配置页的后台 ADB 操作
│   │   ├── ocr_service.py         # OCR 控制服务（模板管理 + 轮询）
│   │   ├── ocr_worker.py          # 单线程 OCR 队列（模板匹配 + PaddleOCR）
│   │   ├── official_data_import_service.py # 官方榜单 OCR、进度与 CSV 原子覆盖
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
│       ├── ai_generation_workflow.py # 攻略和相性生成的对话框/进度工作流
│       ├── shared/                 # 跨页面公开控件、技能弹窗、势力配色缓存
│       │   ├── widgets.py          # DoubleClickLabel
│       │   ├── hero_dialogs.py     # HeroSkillDialog
│       │   └── faction_colors.py   # 势力配色读取/校验/兜底/重载
│       ├── hero_browser.py        # 武将浏览（列表+详情+攻略）
│       ├── hero_edit_dialog.py    # 武将基础字段编辑
│       ├── guide_edit_dialog.py   # 攻略正文和关系编辑
│       ├── hero_relation_select_dialog.py # 攻略关系武将搜索/筛选/多选
│       ├── synergy_edit_dialog.py # 相性评分编辑
│       ├── hero_select_dialog.py  # 武将选择对话框基类
│       ├── recommendation_panel.py # 选将推荐面板（4×2 网格+头像+相性 + 截图导入）
│       ├── hero_card_widget.py    # 推荐卡片（展示、奖牌和交互信号）
│       ├── guide_detail_dialog.py # 推荐卡片的攻略详情弹窗
│       ├── backend_choose_dialog.py # 后端选择（API/浏览器双 Tab）
│       ├── mumu_config_dialog.py  # 模拟器配置对话框（表单、状态和 ROI 框选）
│       ├── official_data_import_dialog.py # 官方榜单图片选择与进度条
│       ├── roi_selector.py        # 模板 ROI 框选对话框（拖拽选区 + 坐标缩放）
│       ├── fetch_dialog.py        # 武将获取选择
│       ├── guide_fetch_dialog.py  # 攻略获取选择
│       ├── synergy_pair_dialog.py # 相性指定获取（选 2~8 武将，自动两两配对）
│       ├── synergy_single_dialog.py # 相性选定武将（选 1 武将）
│       ├── settings_dialog.py     # API 配置对话框
│       ├── data_management_dialog.py # 攻略与相性数据管理对话框
│       └── guide_progress_dialog.py # 攻略生成进度条
├── data/
│   ├── heroes.json                # 165 个武将
│   ├── synergies.json             # 相性评分
│   ├── guides.json                # 武将攻略
│   ├── cards.json                 # 基础卡牌数据
│   ├── 2v2胜率排行.csv            # 2v2 胜率数据
│   ├── 2v2出场排行.csv            # 2v2 出场数据（官方榜单导入生成）
│   ├── 武将放逐.csv                # 武将放逐数据（官方榜单导入生成）
│   └── 武将推荐指数.csv            # 由三份官方榜单计算的推荐指数快照
├── images/
│   └── <武将名>.png               # 165 个武将头像（从官网自动下载）
├── templates/
│   ├── wujiang_select.png         # 武将选择页面模板（用户自行制作）
│   └── wujiang_select.json        # 模板制作时的参考截图尺寸
├── screenshots/                   # 手动截图导出目录
├── screenshot_data/
│   └── latest.json                # OCR 识别结果缓存
├── logs/
│   └── app.log / scraper/ / business/ / subprocess/  # 按模块拆分的日志文件
├── tests/
│   ├── test_*.py                  # 业务、数据、OCR、UI 与配置测试
│   └── test_data/                 # 解析契约样本
├── docs/
│   ├── code_desc/
│   │   ├── summary‌.md             # 项目总览（核心功能、技术栈、模块索引）
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
│   │   ├── call_graph_capture_ocr.md # ADB 截图与 OCR 调用链路
│   │   ├── call_graph_config.md    # 应用入口与配置调用链路
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
python -m ruff check src tests
python -m pytest --collect-only -q
python -m pytest tests/ -v
```

开发环境与 CI 统一使用 Ruff 0.12.0。当前 `pytest --collect-only -q` 收集 **346** 项测试；新增或删除用例后应以该命令输出为准。数据层定向验证可运行：

```bash
python -m pytest tests/test_hero_manager.py tests/test_synergy_manager.py tests/test_guide_manager.py tests/test_data_facade.py -q
```

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
4. 按需启用持续轮询；轮询会检测页面并在匹配后识别
5. 「截图」仅保存当前模拟器画面；选择「📁 从图片导入」才会提交图片到后台 OCR 队列并填充选将推荐

#### OCR 分辨率适配

页面模板与 OCR 识别区域分别保存参考尺寸。识别时，系统会将 `config/ocr_rois.default.json`
中以参考分辨率配置的名称、阵营 ROI 按当前截图宽高分别换算，因此页面比例基本不变时不要求严格固定分辨率。

页面模板匹配也会在参考缩放比例附近尝试多个比例（0.85、0.925、1.0、1.075、1.15），
自动选择置信度最高的结果，再决定是否执行 PaddleOCR。

默认武将名称 ROI 以 2560×1440 为基准，尺寸为 50×145px；高度额外保留 5px 的竖排文字上下缓冲。
识别时日志会记录每个槽位的实际 ROI 坐标、OCR 原始文本和置信度，便于排查截取或识别异常。

```text
ADB 截图
  → 多尺度模板匹配
  → 读取独立的 OCR ROI 配置
  → 换算选将或对局攻略的识别区域
  → PaddleOCR + 武将名库纠正
  → 推荐面板
```

默认布局随版本保存在 `config/ocr_rois.default.json`；通过「配置 → 模拟器配置」的“截图编辑”或
“图片编辑”保存后，修改写入本地 `config/ocr_rois.json`，不会被 Git 更新覆盖。选将推荐包含 8 个名称
区域，对局攻略包含 5 个候选席位的名称和阵营区域。恢复默认会仅移除当前页面的本地覆盖；配置损坏时应用
记录警告并自动回退默认布局。页面模板仍用于匹配页面，官方 UI 同时改变页面特征时需要重新制作对应模板。

---

## 文档导航

- [完整项目细节](docs/project_doc.md)：按模块说明数据、业务、UI、OCR、配置和测试约束。
- [调用图目录](docs/call_graph/)：以 `A() -> B()` 形式记录核心函数调用链和跨进程边界。
- [模块说明](docs/code_desc/)：面向维护和新人培训的分模块摘要。

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
  → TemplateManager.match()（多尺度）→ 武将选择页？
      → 否：静默跳过
      → 是：按参考尺寸换算 ROI
        → GeneralRecognizer.recognize() → 填充推荐面板 8 槽
官方榜单图片 → OfficialDataImportWorker → 表格横线检测 → 名称候选决策 / 胜率模板识别
  → 原子覆盖 data/{2v2胜率排行,2v2出场排行,武将放逐}.csv → 胜率缓存失效
用户操作 → MainWindow → QProcess → 爬虫/AI 脚本
```

### 运行时边界

- QProcess stdout 以字节缓冲保留未完成行，只对完整换行内容做 UTF-8 解码和进度解析；进程结束时 flush 末行。
- 取消任务只调用 `kill()`，由 `finished` 信号异步清理临时文件和更新 UI，不在界面线程同步等待。
- AI 生成每累计 10 条已校验成功结果即原子提交正式 JSON；失败项保留对应旧数据。
- 页面共享控件、技能弹窗和势力配色位于 `src/ui/shared/`；胜率 CSV 由 `src/data/win_rate_repository.py` 统一读取并缓存。

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
report = facade.load_all()   # 一次性加载所有数据并校验跨实体引用
stats = facade.get_stats()   # {heroes: N, synergies: N, guides: N}
facade.heroes.get_hero(114)  # 直接访问各 Manager
facade.heroes.search_heroes("诸葛")  # 模糊搜索
```

各 Manager 功能特性：
- **DataManager[V_co]** — 泛型基类（位于 `manager.py`），定义通用的 `get`/`list_all`/`add`/`update`/`delete`/`load`/`save` 方法
- **HeroManager(DataManager[Hero])** — 武将 CRUD，支持 ID/名称/关键词/势力查询
- **SynergyManager(DataManager[SynergyScore])** — 相性 CRUD，(A,B) 和 (B,A) 自动归一为同一 key
- **GuideManager(DataManager[HeroGuide])** — 攻略 CRUD，以 hero_id 为 key

### 数据完整性与恢复

数据加载采用只读恢复策略。`DataManager.load()` 逐条执行 Pydantic 校验：坏记录和重复键会被跳过并记录为 `DataIssue`，其他合法数据继续可用。`DataFacade.load_all()` 随后校验相性双方、攻略归属及攻略关联 ID；失效关系只从内存中移除，源 JSON 不会被自动覆盖。

```python
report = facade.load_all()
for issue in report.issues:
    print(issue.kind, issue.file_path, issue.entity_key, issue.field_name)
```

当前 UI 会使用恢复后的内存数据，但尚未提供“查看报告”或“一键写回修复”界面。若需要永久修正，请先根据 `report.issues` 人工核对 `data/*.json`，或保留原文件副本后再编辑。

---

## 爬虫模块

### 爬虫核心 (`src/scraper/crawler.py`)

提供 `fetch(binary=True)` / `find_chunk_url()` / `extract_js_array()` / `js_to_json()` / `transform()` / `validate_heroes()` 等公开 API。核心逻辑：

1. 请求官网页面，定位 JS chunk URL
2. 下载 JS chunk，提取 `const e=[...]` 数组
3. JS 语法 → JSON 解析
4. 字段映射（性别数字→枚举、HTML→纯文本拆分技能描述/结算、icon_url 提取）
5. Pydantic 校验

此外，`download_hero_images()` 从原始 JS 数据中提取每个武将的 `icon_url`，经官网域名、响应大小和 PNG 内容校验后原子写入 `images/{武将名}.png`。

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
- 指定增量任务支持跳过已有项
- 输出经过 Pydantic 模型校验
- `--dry-run` 预览 Token 消耗和费用（仅 API 模式）
- 每 10 条校验成功结果原子提交一次正式 JSON；任务结束时提交尾批，失败项保留旧数据
- 双模式：API 直连 / 浏览器自动化（`--browser`，无需 API Key）

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
_save_json() → data/*.tmp → 每批原子替换 data/guides.json / data/synergies.json
```

---

## 桌面应用功能

### 主窗口

| 菜单 | 功能 | 说明 |
|---|---|---|
| 文件 > 退出 | Ctrl+Q | 关闭应用 |
| 配置 > API 配置 | | 编辑 API Key/URL/Model |
| 配置 > 模拟器配置 | | ADB 连接管理 + 模板制作 + OCR 配置 + 持续轮询 |
| 配置 > 数据管理 | | 备份后批量清空武将攻略和相性数据 |
| 数据 > 重新加载数据 | F5 | 重新读取 JSON 文件 |
| 数据 > 官方数据导入 | | 选择 2v2 和/或武将放逐榜单图片；显示当前文件 OCR 进度，覆盖胜率、出场、放逐 CSV，并输出待复核 CSV/行截图 |
| 数据 > 武将获取 > 全量/增量/指定 | | 从官网采集武将（含头像下载） |
| 数据 > 攻略获取 > 全量/增量/指定 | | AI 批量生成攻略 → BackendChooseDialog（API/浏览器） |
| 数据 > 武将相性 > 选定武将 | | 选 1 武将，计算其与全体其他武将的相性 → BackendChooseDialog |
| 数据 > 武将相性 > 指定获取 | | 选 2~8 武将，自动两两配对计算 C(N,2) 组相性评分 → BackendChooseDialog |
| 帮助 > 关于 | | 版本信息 |

AI 批量生成通过 **QProcess** 子进程执行；主窗口菜单将攻略和相性任务委托给 `AiGenerationWorkflow`，统一处理武将选择、后端选择、进度显示和完成后的页面刷新。模板匹配与 OCR 识别由 **OcrWorker** 后台队列处理。ADB 连接与手动截图仍在 GUI 线程同步执行。

### 官方数据导入

从“数据 > 官方数据导入”选择一张或两张官方榜单图片。2v2 图片的左、右表分别写入胜率和出场排行；武将放逐图片的左右栏按视觉行序合并写入放逐排行。导入会覆盖对应正式 CSV，并同时生成 `*_待复核.csv` 与异常行截图，便于检查低置信度、缺字或格式异常记录。确认三份榜单无误后，在“选将推荐”页面点击“重建指数”才会覆盖 `武将推荐指数.csv`。

导入过程在后台线程执行：读取图片和检测表格横线时显示不定进度；行数确定后，进度条显示当前文件的 OCR 工作量。2v2 胜率数字模板准备和逐行识别都会计入进度。

名称识别先在原图放大与增强图的全部结果中选择精确命中武将词表的完整候选；两路精确结果冲突时不按置信度强选。最高结果为单字时按字形逐字补识别；OCR 原文在词表中只有唯一前缀候选时才自动补全。`夏侯`、`司马`等公共前缀对应多个武将，或多个编辑距离候选共享至少两个汉字前缀时，官方导入会按需加载繁体 `chinese_cht` 模型继续确认，不以词表顺序或微小视觉分差决胜。模型不可用或仍无法唯一确认时保留原文，并以“武将名称候选不唯一”写入待复核。该策略只作用于官方榜单导入，不改变截图、文件导入和轮询的常规武将识别。

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
- 左侧展示**武将头像**（从 `images/` 读取），名称半透明浮在底部，左上角势力色块；右侧首行以“定位 · 推荐指数”并列展示，例如 `辅助 · 推荐指数：92 / S`，避免重复名称和势力
- 右侧突出显示**推荐指数**（当前版本全服数据计算的“推荐指数：分数 / 评级”，缺失时显示“推荐指数：-- / 数据不足”）、**高相性组合**、**胜率**（从 `2v2胜率排行.csv` 读取），胜率前三自动标记 🥇🥈🥉 奖牌
- “选将推荐”和“对局攻略”共用 18px 页面标题与紧凑型顶部主、次操作按钮样式
- 指数正常时不重复显示数据状态；官方榜单更新后显示“推荐指数待重建”提示及“立即重建”，日常“保存截图”和“重建推荐指数”入口位于“更多”菜单
- 对外提供 `update_recommendations(data: list[dict])` 接口，接收 `{index, name, confidence}` 格式数据
- 支持两种导入方式：
  - **截图** — 通过 ADB 截取并保存模拟器屏幕画面，不自动触发 OCR
  - **从图片导入** — 选择本地游戏截图文件 → PaddleOCR 识别 → 填入槽位
- 空状态直接提供“识别当前阵容”和“从图片导入”入口
- 轮询模式开启后，定时检测模拟器画面是否为武将选择页，自动识别并填充

### 武将浏览器

- 左侧列表支持**搜索过滤** + **势力筛选**，右侧展示当前武将摘要
- Tab 切换「武将信息」「攻略指南」和「武将相性」；相性表可按搭档/评级筛选，双击说明查看 Markdown，双击其他列编辑评分
- 技能展示：描述 + 可折叠的结算详情
- 攻略展示：首屏“核心建议”突出核心要点与对抗建议；点击“阅读完整攻略”后，可在攻略正文预览上双击打开独立 Markdown 阅读窗口
- 克制/搭配关系支持点击标签跳转到对应武将
- Tab 栏右上角：武将信息和攻略指南各有独立的"修改"+"删除"按钮（绿色/红色），点击后弹出 `HeroEditDialog` / `GuideEditDialog` 编辑弹窗
- 攻略编辑弹窗中的“被克制”和“搭配推荐”使用可搜索、可按势力筛选的多选武将弹窗，支持预选回填、全选当前筛选和清空选择
- 相性列表的“修改”打开 `SynergyEditDialog`；评分变更会同步刷新评级，保存后写入对应相性记录
- 攻略展示中的关系武将标签使用自适应流式布局，可点击跳转；势力筛选下拉框复用选将推荐的势力配色，支持可删除标签、搜索、全选和反选，超过 5 个势力时显示前 5 个及剩余数量
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
│   └── business.log         # 业务服务日志
├── data/
│   └── data.log             # 数据管理日志
├── ocr/
│   └── ocr.log              # OCR 日志
├── capture/
│   └── capture.log          # ADB 截图日志
└── subprocess/
    ├── stdout.log           # 子进程标准输出
    └── stderr.log           # 子进程错误输出
```

每个文件最大 10MB，保留 5 个备份。桌面应用和直接运行 CLI 会读取 `config.env` 中的 `LOG_LEVEL`、`LOG_TO_FILE`；由桌面应用启动的 QProcess 子进程只通过 stdout/stderr 交给主进程统一记录，避免多进程同时轮转同一文件。

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

### 势力配色配置

通过“配置 → 势力配色”打开紧凑列表。每行仅显示势力名称、颜色小方块和 Hex 值；点击颜色小方块会打开支持 HSB 调整与屏幕取色的 Color Picker 浮层，保存后立即刷新已显示的势力标签。
该页面及颜色浮层按钮已汉化；Qt 按钮样式使用兼容性更好的 `background-color` 属性。

---

## 攻略指定获取状态筛选

“数据 → 攻略获取 → 指定获取”对话框会为每位武将展示攻略状态，并支持按状态筛选，避免重复生成攻略。

| 状态 | 判定规则 | 默认处理 |
|------|----------|----------|
| 未生成 | `GuideManager` 中不存在该武将的攻略记录 | 默认筛选并建议生成 |
| 已有攻略 | 攻略更新时间不早于武将资料更新时间 | 通常无需重新生成 |
| 待更新 | 已有攻略，但攻略更新时间早于武将资料更新时间 | 建议重新生成 |

- 默认仅显示“未生成”的武将；筛选项同时显示各状态数量，用户可切换至“待更新”“已有攻略”或“全部”。
- 列表项显示武将名、势力和状态标签；仅选已有攻略或待更新攻略时，确认按钮显示“重新生成 N 篇攻略”；混选时会标明重新生成数量。
- 选择对话框同时使用 `HeroManager` 的资料更新时间和 `GuideManager` 的攻略更新时间。日期缺失或格式异常时保守判定为“待更新”。

## 后期待开发功能记录

### 相性指定获取状态筛选与数据健康总览

**当前状态：条件触发，暂不排期。** 攻略指定获取已具备“未生成 / 待更新 / 已有攻略”筛选；相性不能直接复用这套规则。相性的状态单位是武将对，而非单个武将；此外，全量相性生成会按评分阈值移除低分结果，因此 `SynergyManager` 中不存在某一对记录，并不必然表示该对从未计算。

**已完成前置：**`SynergyScore.last_updated` 已记录每条已收录相性最后成功生成的日期。该字段只能判断已收录结果的新旧，不能替代后续用于识别“已计算未收录”的独立生成记录。

本功能在不改变现有攻略流程的前提下，分两期推进：

1. **相性状态追踪与指定获取：**新增独立的相性生成记录，按排序后的武将 ID 保存最后一次成功计算时间、两名武将资料更新时间快照和结果类型。相性指定获取保留“选择 2~8 名武将”的第一步，第二步列出所有武将对，支持按状态筛选并确认实际要生成的配对。
2. **数据管理健康总览：**在“配置 → 数据管理”中展示攻略和相性的状态统计与可筛选详情；该页面只提供诊断和跳转入口，不直接启动 AI 任务，也不将状态筛选结果默认绑定到清空操作。

相性状态的预期含义如下：

| 状态 | 判定规则 | 默认处理 |
|------|----------|----------|
| 未计算 | 没有该武将对的成功生成记录 | 默认勾选，建议生成 |
| 待更新 | 成功计算时间早于任一参与武将的资料更新时间 | 建议重新生成 |
| 已收录 | 已成功计算且评分结果保留在 `synergies.json` 中 | 默认不生成 |
| 已计算未收录 | 已成功计算，但结果因评分阈值未保留 | 默认不生成 |
| 历史状态未知 | 旧 `synergies.json` 记录缺少可信的计算时间 | 不自动生成，由用户决定 |

#### 考虑开发的触发条件

满足以下任一真实业务条件后，才评估启动第一期；只有第一期的状态记录稳定后，才启动第二期：

- 同一武将对在一个版本周期内被重复提交生成至少 2 次，且重复原因是界面无法区分“未计算”和“低分未收录”；
- 武将资料更新后，用户需要连续手工找出至少 5 组相关相性重新计算，现有“已有则跳过 / 全部覆盖”无法满足更新范围控制；
- 用户一次选择 5 名以上武将时，已有配对、待更新配对和待生成配对混杂，当前汇总数量无法支持明确确认本次成本；
- 相性生成的 Token 或等待时间已经形成可观察的重复成本，例如连续两次任务均包含可确认的重复配对；
- 数据核查中发现相性记录引用已删除武将、攻略关系引用失效或状态统计与实际 JSON 内容不一致，并且需要反复人工定位；
- 已有用户需要按“待更新”“历史未知”等状态批量检查数据，而不是只查看攻略/相性的总条数。

#### 开发前置条件

启动前必须先满足：

- 明确全量相性、指定配对和选定武将三种生成模式对低分结果的保留策略；不能将“无正式记录”直接视为“未计算”；
- 确定相性生成记录的数据格式、备份策略与历史数据迁移策略；旧记录不得伪造为当前时间；
- 相性正式结果和生成记录必须在同一成功批次提交；失败、取消和未通过校验的结果不得写入成功记录；
- 明确第二步配对列表向 CLI 传递明确的武将对，避免继续以 `--update` 覆盖所选武将形成的全部配对；
- 为历史未知、低分未收录、武将资料日期异常准备人工可理解的显示和保守默认行为；
- 准备可复放测试数据，至少覆盖未计算、已收录、低分未收录、待更新、历史未知、失败和取消七类情况。

#### 上线验收门槛

- 相性指定获取显示的是配对状态，不把单个武将错误标为“已有相性”或“待更新”；
- 默认操作只提交“未计算”配对，用户选择重新生成后只覆盖明确选中的配对；
- 已计算但低分未收录的配对不会因缺少 `SynergyScore` 记录而被反复提交；
- 武将资料更新后，相关配对能稳定归入待更新，未关联武将的配对状态不受影响；
- 生成成功、失败、取消和写入异常后，`synergies.json` 与生成记录不出现相互矛盾的状态；
- 数据管理状态筛选为只读诊断，既有“清空数据”的备份、输入确认和任务运行中禁用行为保持不变；
- 攻略指定获取现有的三态筛选、按钮文案和生成流程不得回归。

#### 暂不开发的情况

当前相性指定获取已能通过“跳过已有 / 重新生成并覆盖”满足实际需求，尚无重复成本证据，或无法先定义低分结果与历史数据的可靠状态时，不启动开发。数据管理也不应在缺少稳定相性状态记录时提前展示误导性的“未计算”统计。

### 常规截图名称 OCR 加速

**当前状态：暂不实施。** 连接模拟器后已在后台预热 PaddleOCR 模型，首次识别不再承担模型初始化时间。名称 OCR 的预处理、推理和词表纠错尚未调整，优先保证复杂背景、竖排姓名和低清截图的识别准确性。

仅在 `logs/business/business.log` 的阶段耗时持续显示名称预处理、名称 OCR 或名称纠错为主要瓶颈，且同一批回归截图的名称准确率稳定时再启动。实施前需保留代表性的选将推荐与对局攻略截图；分别记录优化前后的总耗时、名称 OCR 耗时、识别结果和置信度。候选措施按低风险到高风险依次为：复用预处理结果、缩减无效 ROI 边缘、调整放大倍率或 OCR 批处理。每一步只调整一项，并要求名称、阵营及敌我席位结果不回退；否则撤销该项优化。

### 官方榜单 OCR 第二阶段：识别率优化

**当前状态：条件触发，暂不排期。** 第一阶段已经为复姓公共前缀、多路候选冲突和未命中词表结果增加安全护栏；无法唯一确认时会保留 OCR 原文并进入待复核，避免把“夏侯”等模糊结果静默绑定到错误武将。第二阶段不再补充防误绑规则，而是尝试从图片中恢复缺失的区分字符，减少长期人工复核。

计划按以下顺序拆分实施，每一步独立验证，不一次性修改全部 OCR 行为：

1. **建立可回放样本：**保存名称格截图、人工正确答案、原图/增强图/逐字/繁体模型的全部候选、置信度、字形切分位置和最终决策原因。该步骤只增加离线诊断能力，不改变正式识别结果。
2. **改进偏旁字形分割：**根据正常字符中位宽度、窄偏旁宽度、相邻间距和合并后宽度，识别类似“惇”的 `忄` 与右侧主体并尝试合并。规则必须适用于通用偏旁结构，不得硬编码“惇”或“夏侯惇”，也不使用可能粘连相邻汉字的无约束全局膨胀。
3. **融合多路候选证据：**综合候选来源、字符数量、词表精确命中、编辑距离唯一性和多路结果一致性。置信度只在相同证据层级中比较，不能让高置信度缺字结果压过包含完整区分字符的候选；证据冲突时仍回退到待复核。

#### 考虑开发的触发条件

满足以下任一真实业务条件后，才评估启动第二阶段：

- 官方新增多个同复姓人物，使现有榜单样本开始稳定进入“武将名称候选不唯一”流程；
- 同一类偏旁与主体分离问题连续影响至少 3 个真实样本，而不是只出现一次的偶发现象；
- 连续两次官方数据更新都需要人工修正同一批名称，人工复核已经成为重复成本；
- 名称歧义使胜率、出场排名或推荐指数持续显示“数据不足”，开始影响正常使用；
- 单张 160 行榜单中，同一字形切分根因造成的待复核达到 2 行以上；
- 简体和繁体模型都无法稳定提供精确结果，现有安全降级无法满足导入效率要求。

如果出现模糊结果被自动绑定到另一个合法武将的真实案例，应作为数据安全缺陷立即单独修复，不等待第二阶段排期。

#### 开发前置条件

启动开发前必须先满足：

- 至少保留现有三张夏侯惇真实样本，以及司马炎、司马相如、司马懿等复姓对照样本；
- 纳入“曹不→曹丕”“荀或→荀彧”“张邰→张郃”“魏答→魏咎”等现有正确纠偏样本；
- 准备至少 50 个当前可以正确识别的普通名称格作为控制组；
- 所有样本都有人工确认的正确名称，并能离线批量回放；
- 能分别评估偏旁合并和候选融合的影响，不把两类修改混在一次验证中。

#### 上线验收门槛

- 控制组、现有正式数据和当前 30 条真实待复核样本不得出现新的自动误绑或正式名称回归；
- 原有单姓纠偏及普通对局截图 OCR 结果保持不变；
- 扩展夏侯词表后，模糊结果不得自动绑定到错误人物；
- 自动确认必须具备精确命中、唯一映射或多路一致证据，无法确认时仍回到第一阶段的安全待复核路径；
- 目标根因造成的人工复核数量应明显下降，建议至少减少 50%；
- 单次官方数据导入总耗时增长建议控制在 15% 以内。

#### 暂不开发的情况

当前正式结果仍正确、只有孤立样本、缺少人工标注控制组，或者方案依赖具体武将硬编码和未经验证的评分权重时，不启动第二阶段。只有安全护栏已经稳定、人工复核开始形成持续成本，并且具备可回放验证数据后，才进入实际开发。
