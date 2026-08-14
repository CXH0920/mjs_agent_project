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
│   │   ├── official.py            # 官网全量采集兼容 CLI
│   │   ├── incremental.py         # 增量/指定采集兼容 CLI
│   │   ├── ai_batch.py            # AI 批量生成兼容 CLI
│   │   ├── official_source/       # 官网适配、清洗、全量与增量实现
│   │   │   ├── adapter.py
│   │   │   ├── crawler.py
│   │   │   ├── full.py
│   │   │   └── incremental.py
│   │   └── ai/                    # AI 生成、双后端、Prompt 与 JSON 提取
│   │       ├── batch.py
│   │       ├── generation.py
│   │       ├── api_generator.py
│   │       ├── browser_generator.py
│   │       ├── browser_session.py
│   │       ├── prompt_utils.py
│   │       ├── json_extract.py
│   │       └── utils.py
│   ├── business/
│   │   ├── fetching/              # 武将采集、AI 生成进程与相性重载
│   │   ├── emulator/              # 截图、ADB 后台操作与 MuMu 配置协调
│   │   ├── recognition/           # OCR 控制、唯一 worker 与官方榜单导入
│   │   ├── analysis/              # 选将推荐与对局攻略分析
│   │   └── maintenance/           # 数据清理、修复与事务化修改
│   ├── capture/
│   │   ├── __init__.py
│   │   ├── adb_screen.py          # ADB 连接与截图（subprocess exec-out 无文件中间态）
│   │   ├── prober.py              # MuMu 设备自动探测（注册表/环境变量/常见路径）
│   │   └── image_utils.py         # PIL ↔ QPixmap / 剪贴板 / 图像保存
│   ├── ocr/
│   │   ├── __init__.py
│   │   ├── template_manager.py    # OpenCV 模板匹配（TM_CCOEFF_NORMED，<50ms）
│   │   ├── recognizer.py          # PaddleOCR + 字数门禁 + 候选闭包内评分
│   │   └── ocr_loader.py          # 模板管理器单例
│   └── ui/
│       ├── app/                    # 主窗口、应用图标、翻译与轮询编排
│       ├── library/                # 武将资料、卡牌图鉴及编辑弹窗
│       ├── recommendation/         # 选将推荐页面与推荐卡片
│       ├── match/                  # 对局阵容状态、攻略页面与分析视图
│       ├── generation/             # AI 生成工作流、选择和进度弹窗
│       ├── configuration/          # API、模拟器、势力配色与 ROI 配置
│       ├── data_admin/             # 数据管理与官方榜单导入
│       └── shared/                 # 跨功能控件、展示与样式
│           ├── style.py            # 视觉 Token、全局 QSS 与语义角色
│           ├── widgets.py          # 页面标题、空状态、标准底栏与 Toast
│           ├── hero_dialogs.py     # HeroSkillDialog
│           └── faction_colors.py   # 势力配色读取/校验/兜底/重载
├── data/
│   ├── heroes.json                # 165 个武将
│   ├── synergies.json             # 相性评分
│   ├── guides.json                # 武将攻略
│   ├── cards.json                 # 基础卡牌数据
│   ├── 2v2胜率排行.csv            # 2v2 胜率数据
│   ├── 2v2出场排行.csv            # 2v2 出场数据（官方榜单导入生成）
│   ├── 武将放逐.csv                # 武将放逐数据（官方榜单导入生成）
│   ├── 武将推荐指数.csv            # 由三份官方榜单计算的推荐指数快照
│   └── char_info_cache.json       # 汉字特征用户层缓存（运行时自动扩展，不提交）
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
│   ├── retrospective.md            # 历史负面事件复盘与避坑指南
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

开发环境与 CI 统一使用 Ruff 0.12.0。测试数量以 `python -m pytest --collect-only -q` 实时输出为准，不在文档中维护具体数字。数据层定向验证可运行：

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

> **公告更新检查**（应用内手动触发，无 CLI）：菜单“数据 > 检查公告更新”拉取官方公告 API（单次 5 条全文），仅对带 `【新增武将】/【武将调整】` 章节的公告判定为武将相关；同时拉取官网百科逐武将内容哈希与本地快照做 diff，公告提及且 diff 确认后才提示“可更新”——避免公告发布早于百科生效（通常滞后半天到一天）导致的空跑。

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


### RAG 攻略语料增强
攻略生成（API/浏览器双模式）默认检索 RAG 官方规则语料（`data/rag_corpus` + `data/rag_index`）注入 prompt，提升规则准确性；语料与索引由 `mjs_rag_project` 维护，本仓库通过一键管道同步。
- 禁用增强：`python -m src.scraper.ai_batch --guide --no-rag`
- 重建索引：`python -m src.scraper.ai_batch --rebuild-rag-index`
- 一键维护管道（本地）：`python scripts/maintain_rag.py --force --build-index`（重建语料与向量索引），或使用应用内「知识库维护」页面可视化执行
- 配置项：`config.env` 中 `RAG_ENABLED` / `RAG_MODEL_DIR` / `RAG_TOP_K` / `RAG_PROMPT_CHARS` / `RAG_PROJECT_DIR`
维护脚本（`maintain_rag.py` / `rag_audit.py` / `build_*.py`）已收编到 `scripts/`，全部在 test_project 本地运行（数据源 `data/`、文档源 `docs/`）：
- 查看人工补充清单：`python scripts/rag_audit.py`
- 预览语料状态：`python scripts/maintain_rag.py --check`
- 增量重建语料/索引：`python scripts/maintain_rag.py --build-index`
- 预览官方数据差异：`python scripts/import_from_test.py --dry-run`
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

本地导入和 ROI 图片编辑仅接受实际 PNG/JPEG 内容，ADB 截图仅接受实际 PNG；所有入口均限制为 6 MiB 和 400 万像素，解压炸弹警告会作为错误处理。

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
- [UI 设计系统](docs/spec/spec_ui_design_system.md)：视觉 Token、组件语义、交互状态和三档窗口验收规则。
- [UI 导航规范](docs/spec/spec_ui_navigation.md)：目标信息架构、页面操作归属和状态保持规则。
- [调用图目录](docs/call_graph/)：以 `A() -> B()` 形式记录核心函数调用链和跨进程边界。
- [模块说明](docs/code_desc/)：面向维护和新人培训的分模块摘要。
- [历史复盘与避坑指南](docs/retrospective.md)：汇总 Codex/Claude 历史负面事件、根因、预防门禁和未闭环风险。

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
官网页面 → official_source/crawler.py 解析 JS chunk → full.py/incremental.py 清洗校验 → data/heroes.json
                                                                        ↘ images/<武将名>.png
DeepSeek API / 网页版 → ai/batch.py → api_generator.py / browser_generator.py
  → generation.py → data/{guides,synergies}.json
data/*.json → DataFacade (三个 Manager) → UI 展示

模拟器屏幕 → ADB screencap → PIL Image（全在内存，无磁盘 I/O）
  → TemplateManager.match()（多尺度）→ 武将选择页？
      → 否：静默跳过
      → 是：按参考尺寸换算 ROI
        → GeneralRecognizer.recognize() → 填充推荐面板 8 槽
官方榜单图片 → CaptureService.submit_official_import() → OcrWorker → 表格行检测 → 名称候选决策 / 胜率模板识别
  → 榜单唯一性补全与名称门禁 → 原子覆盖 data/{2v2胜率排行,2v2出场排行,武将放逐}.csv → 胜率缓存失效
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

### 爬虫核心 (`src/scraper/official_source/crawler.py`)

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

### 公告监控 (`src/scraper/official_source/announcement.py`)

公告列表页是 Nuxt 对公开 JSON API 的 SSR 展示，底层接口：

```
GET https://ucmsv2api.ztgame.com/api/news/list?site=mjs&type=notice&page=1&per_page=5
```

- `fetch_latest_announcements()` — 单次获取 5 条公告全文；API 失败时回退解析 `notice-1.html` 的标题/日期/链接
- `classify_hero_related()` — 仅按 `【新增武将】/【武将调整】` 章节标题判定相关；正文其他位置提及武将名（修复列表、副本内容）不算
- `hero_content_hash()` / `build_hero_snapshot()` / `diff_heroes()` — 官网字段规范化后 md5，与 `data/baike_snapshot.json` 逐武将比对，输出新增/修改/删除

---

## AI 批量生成

```
src/scraper/
├── ai_batch.py          兼容 CLI 入口
└── ai/
    ├── batch.py         参数解析、配置加载与任务分发
    ├── api_generator.py API 调用核心
    ├── browser_generator.py # 浏览器自动化生成器
    ├── browser_session.py   # Playwright + Edge 会话
    ├── generation.py    四种生成编排函数
    ├── prompt_utils.py  Prompt 构建与成本估算
    ├── json_extract.py  AI 回复 JSON 提取
    └── utils.py         数据加载、校验与原子保存
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

主窗口采用统一应用外壳：左侧 `NavigationRail` 在“资料库 / 选将推荐 / 对局攻略”三个长期工作区间切换，顶部 `ContextHeader` 显示当前上下文及页面操作。内容区继续复用同一个主 `QTabWidget`，仅隐藏主 `TabBar`，因此页面实例、搜索条件、滚动位置和识别结果不会因导航切换而重建；资料库内部仍以“武将资料 / 卡牌图鉴”二级页签切换。OCR 自动跳转继续调用 `setCurrentWidget()`，再由 `currentChanged` 同步左侧选中态和顶部标题。

窗口宽度小于 1040px 时导航强制折叠，恢复宽屏后还原用户本次会话的折叠选择。原菜单栏作为兼容入口保留，并与顶部操作、设置菜单共享同一组 `QAction` 和快捷键。底部状态栏分别承担任务/数据进度、模拟器 ADB 状态和 OCR 轮询状态；后两项保持常驻且可进入模拟器配置。

| 菜单 | 功能 | 说明 |
|---|---|---|
| 文件 > 退出 | Ctrl+Q | 关闭应用 |
| 配置 > API 配置 | | 编辑 API Key/URL/Model |
| 配置 > 模拟器配置 | | ADB 连接管理 + 模板制作 + OCR 配置 + 持续轮询 |
| 配置 > 数据管理 | | 备份后批量清空武将攻略和相性数据 |
| 数据 > 重新加载数据 | F5 | 重新读取 JSON 文件 |
| 数据 > 官方数据导入 | | 选择 2v2 和/或武将放逐榜单图片；显示当前文件 OCR 进度，覆盖胜率、出场、放逐 CSV，并输出待复核 CSV/行截图 |
| 数据 > 检查公告更新 | | 拉取官方公告 + 百科逐武将 diff，武将相关新公告提醒；状态流转：待生效 → 可更新 → 已处理；60 秒冷却，忙碌/冷却中弹窗提示 |
| 数据 > 公告记录 | | 查看公告全文与百科 diff 变更清单；一键“更新武将数据”（弹确认对话框展示字段级差异与 Git 风格全文 diff，勾选后指定获取 + 增量精准覆盖；未勾选保留本地）；状态栏进度条显示联网/采集进度 |
| 数据 > 武将获取 > 全量/增量/指定 | | 从官网采集武将（含头像下载） |
| 数据 > 攻略获取 > 全量/增量/指定 | | AI 批量生成攻略 → BackendChooseDialog（API/浏览器） |
| 数据 > 武将相性 > 选定武将 | | 选 1 武将，计算其与全体其他武将的相性 → BackendChooseDialog |
| 数据 > 武将相性 > 指定获取 | | 选 2~8 武将，自动两两配对计算 C(N,2) 组相性评分 → BackendChooseDialog |
| 帮助 > 关于 | | 版本信息 |

AI 批量生成通过 **QProcess** 子进程执行；主窗口菜单将攻略和相性任务委托给 `AiGenerationWorkflow`，统一处理武将选择、后端选择、进度显示和完成后的页面刷新。常规识别、模型预热和官方榜单整批导入由唯一的 **OcrWorker** 后台队列按提交顺序处理。ADB 连接与手动截图仍在 GUI 线程同步执行。

配置、数据管理、编辑、选择、导入、进度、ROI 和详情弹窗统一使用标题区、内容区与固定底栏。底栏右侧按“取消 / 保存或确认”排列；提交期间禁用重复操作。普通保存和导入成功使用非模态 Toast，失败继续使用模态错误提示并保留可恢复输入；清空、删除和归档保留危险按钮与二次确认，关键删除或清空完成后使用模态结果反馈。

### 官方数据导入

从“数据 > 官方数据导入”为每类榜单选择一张或多张图片。列表顺序就是分页合并顺序，可通过上移、下移调整；旧版长图只需选择一张。系统会自动识别旧版长图和新版分页版式，2v2 每页左、右表分别追加到胜率和出场排行，武将放逐按每页左栏、右栏的视觉顺序追加。全部页面完成结构与排名顺序校验后才会覆盖对应正式 CSV；错序、重复图片、混用新旧版式或页面结构异常均不会写入正式数据。导入同时生成 `*_待复核.csv` 与异常行截图，复核记录包含来源图片和页序号，便于检查低置信度、缺字或格式异常记录。确认三份榜单无误后，在“选将推荐”页面点击“重建指数”才会覆盖 `武将推荐指数.csv`。

导入过程作为一个整批任务进入唯一 `OcrWorker`：若模型仍在预热会自动排队等待，开始后独占完成所选榜单，常规 OCR 任务在队列中等待。主窗口在导入对话框打开期间暂停自动轮询，关闭后按当前连接与配置恢复。读取图片、识别版式和定位数据行时显示当前页；所有页行数确定后，进度条显示该类榜单的总 OCR 工作量。2v2 胜率数字模板准备和逐行识别都会计入进度，完成摘要通过 Toast 展示。

名称识别先在原图放大与增强图的全部结果中选择精确命中武将词表的完整候选；两路精确结果冲突时不按置信度强选。最高结果为单字时按字形逐字补识别；OCR 原文在词表中只有唯一前缀候选时才自动补全。公共前缀对应多个武将时按需加载繁体 `chinese_cht` 模型，但繁体原文及编辑距离纠正结果必须仍属于简体 OCR 产生的候选白名单。仍未确认的名称在整榜完成后排除已占用候选，只有剩余一个且无其他行竞争时才补全。最终存在未知名、重复名或同规模输出集合不一致时，导入仅保存待复核 CSV/截图并保留原正式数据。该策略只作用于官方榜单导入，不改变常规武将识别。

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

- 结果区固定为 **2 列 × 4 行**，每张卡片高 141px、宽 390～640px；默认 1100×760 窗口完整展示 8 卡，960×640 只纵向滚动
- 左侧展示紧凑头像、名称浮层与势力标签；右侧依次展示定位、推荐指数、最佳搭档、两条相性摘要、历史单将胜率、技能与攻略操作，完整相性列表保留在悬停提示中
- 历史单将胜率前三使用固定尺寸的 `胜率 TOP 1/2/3` 徽章和排名边框；它只表达当前八名武将的单将胜率排序，不代表阵容强度
- 页面操作行只保留识别状态、主要识别操作和“更多”；图片导入、保存截图与重建推荐指数收纳到“更多”菜单，空状态直接提供识别和图片导入
- 图片缺失、攻略缺失、指数过期、指数数据不足、OCR 未知或待确认均使用文字与语义色同时表达；识别失败在页内提供可恢复通知
- “查看攻略”使用蓝色强调的次要按钮；推荐指数口径入口始终保留，指数数据不足时仍可查看计算口径
- 官方榜单更新后显示“推荐指数待重建”通知和“立即重建”；用户确认后重建会同时刷新当前卡片的指数、历史单将胜率和 TOP 排名
- 对外提供 `update_recommendations(data: list[dict])` 接口，接收包含 `index`、确认后的 `name`、`raw_name`、`candidates`、`resolution`、`confidence` 和 `evidence` 的结构化结果；旧的 `{index, name, confidence}` 仍兼容
- 支持识别当前模拟器画面、从本地图片识别，以及在“更多”菜单中仅保存当前截图
- 空状态直接提供“识别当前阵容”和“从图片导入”入口
- 轮询模式开启后，定时检测模拟器画面是否为武将选择页，自动识别并填充

### 对局攻略

- 页面使用不可折叠的 42% / 58% 水平分割：左侧完成阵容核对，右侧展示总览、我方打法、对抗敌方和单将详情
- 左侧确认区固定在顶部，四张 176～250px 宽的紧凑卡片在独立纵向滚动区内按“我方 / 敌方 / 未定”分组
- 每张卡片使用“我方 / 敌方 / 未定”互斥分段控件，并稳定标记“我、队友、敌方 1、敌方 2”；确认按钮不会随卡片重排移动
- 攻略总览优先展示本局行动顺序；我方信息使用中性蓝色，红色只用于敌方威胁，缺失攻略或胜率收纳在可展开提示中
- 重复识别、未知武将、人数超限和修改后的重新确认继续由 `LineupState` 处理；新 OCR 结果会清除旧分析并回到总览
- 两侧滚动区均禁用横向滚动，长武将名和攻略文本自动换行

### 武将浏览器

- 左侧列表支持**搜索过滤** + **势力筛选**，右侧展示当前武将摘要
- Tab 切换「武将信息」「攻略指南」和「武将相性」；相性表可按搭档/评级筛选，双击说明查看 Markdown，双击其他列编辑评分
- 技能展示：描述 + 可折叠的结算详情
- 攻略展示：首屏“核心建议”突出核心要点与对抗建议；点击“阅读完整攻略”后，可在攻略正文预览上双击打开独立 Markdown 阅读窗口
- 克制/搭配关系支持点击标签跳转到对应武将
- 当前武将身份头部保留一个随页签切换的“编辑”按钮，删除入口收纳在相邻“更多”菜单；三类操作分别打开 `HeroEditDialog`、`GuideEditDialog` 和 `SynergyEditDialog`
- 攻略编辑弹窗中的“被克制”和“搭配推荐”使用可搜索、可按势力筛选的多选武将弹窗，支持预选回填、全选当前筛选和清空选择
- 相性列表双击非说明列或点击“编辑相性”打开 `SynergyEditDialog`；双击说明列使用统一详情弹窗阅读 Markdown
- 攻略展示中的关系武将标签使用自适应流式布局，可点击跳转；势力筛选下拉框复用选将推荐的势力配色，支持可删除标签、搜索、全选和反选，超过 5 个势力时显示前 5 个及剩余数量
- 编辑器返回重新校验的模型副本；保存失败时原数据不变并重新显示保留输入的弹窗，成功后自动刷新并使用 Toast 反馈，删除完成使用模态结果

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
│   ├── official.log         # 官网采集及其 QProcess 输出
│   └── ai_generation.log    # AI 生成及其 QProcess 输出
├── business/
│   ├── fetching.log         # 采集与生成进程编排
│   ├── emulator.log         # 截图、ADB 与 MuMu 协调
│   ├── recognition.log      # OCR 调度与官方榜单导入
│   └── business.log         # 分析、维护及其他业务日志
├── data/
│   └── data.log             # 数据管理日志
├── ocr/
│   └── ocr.log              # OCR 日志
├── capture/
│   └── capture.log          # ADB 截图日志
└── subprocess/
    └── unclassified.log     # 未声明工作流的子进程输出
```

每个文件最大 10MB，保留 5 个备份。桌面应用和直接运行 CLI 会读取 `config.env` 中的 `LOG_LEVEL`、`LOG_TO_FILE`；由桌面应用启动的 QProcess 子进程只通过 stdout/stderr 交给主进程统一记录，并按官网采集或 AI 生成路由到对应 scraper 日志，避免多进程同时轮转同一文件。AI 日志只记录任务、长度、字段、用量和错误摘要，不记录 Prompt、完整回复、解析正文、认证信息或 `reasoning_content`。

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
| 六 | 选将推荐（2 列 × 4 行固定卡片+头像+数据接口） | ✅ 已完成 |
| 七 | 浏览器自动化（Playwright + Edge）双模式 AI 生成 | ✅ 已完成 |
| 八 | 屏幕采集（MuMu ADB 截图 + 模板匹配 + PaddleOCR 识别 + 持续轮询） | ✅ 已完成 |
| 九 | 推荐引擎（相性查询、胜率 CSV、OCR 数据导入） | ✅ 已完成 |
| 十 | 武将编辑与攻略编辑（tab-header 级修改/删除按钮 + 编辑弹窗） | ✅ 已完成 |
| 十一 | 相性配对多武将组合（最多 8 武将 × 两两配对） | ✅ 已完成 |
| 十二 | 公告监控（手动检查 + 百科逐武将 diff + 精准更新） | ✅ 已完成 |

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

**当前状态：已实施。** 应用启动时即在唯一 `OcrWorker` 中预热 PaddleOCR，不依赖模拟器连接；状态按 `idle → warming → ready/failed` 反馈到状态栏，失败后允许重新提交。预热加载词表字符特征，并执行一次名称拼图尺寸的检测与直接识别推理，避免首次实际任务承担模型和运行时算子初始化。Windows 下首次加载期间会隐藏 Paddle 依赖探测产生的短命令窗口，避免无控制台启动时连续闪窗。ADB 截图会将图像副本提交 OCR worker，并由独立的单线程保存器压缩原图；OCR 完成不等待 PNG，保存完成另发 `image_saved`。本地导入仍返回已有源文件路径。

常规截图的同类 ROI 会横向拼图后只调用一次 PaddleOCR 检测：选将页一次名称拼图；对局攻略分别进行名称和阵营拼图。每个名称槽位记录批量增强图证据；缺失、多候选、冲突或置信度低于 0.8 时，才追加增强图与仅放大原图的逐槽识别。名称解析先做长度分流：精确命中直接确认；严格前缀视为缺字，只保留前缀白名单，且唯一前缀至少识别出 2 个字符才可确认；与候选等长且仅错一字时，唯一候选需通过 0.55 字形门槛，多候选则仅在 OCR 置信度至少 0.7、最高字形分至少 0.35、领先第二名至少 0.15，并得到 `enhanced` 与 `plain` 两个独立证据族一致支持时确认，状态为 `multi_similarity`；其他增删字情况保持待确认。若同一原文同时命中长名严格前缀和等长候选，则合并两类候选并标记 `length_mode=uncertain`，不自动补全。

多路证据的非空候选集合取交集，交集为空即 `conflict`，任何精确或纠正结果都不能跨候选白名单覆盖。页面唯一性只消歧原本有多个候选且 `length_mode` 为 `missing/complete` 的槽位，不会提升 `uncertain` 或未过安全门槛的单候选。结果携带 `raw_name`、`candidates`、`resolution`、`length_mode` 和 `evidence`，选将推荐允许在候选内人工确认，对局攻略在全部名称确认前禁止确认阵容；自动轮询只统计已确认名称。名称 ROI 的卡框和底部定位字会干扰像素字符分割，因此当前不以视觉字符数作为硬门禁。势力关联仅保留为后续可选证据：只能过滤已有候选，不能扩展候选，本次未实现。`src/data/char_info_cache.json`（基线）应覆盖当前英雄名全部字符和常见误识字；更新 `data/heroes.json` 后运行 `python scripts/build_character_feature_cache.py` 同步基线缓存。运行时新增武将时，缺失字符会动态构建并写入 `data/char_info_cache.json`（用户层缓存，gitignored，加载时自动合并），无需手动维护。

### 官方榜单 OCR 第二阶段：识别率优化

**当前状态：条件触发，暂不排期。** 第一阶段已经为公共前缀、多路候选冲突、罕见字模型越界纠正和未命中词表结果增加安全护栏，并可利用榜单内部唯一性补全有充分证据的名称。第二阶段主要尝试从图片中直接恢复缺失的区分字符，减少仍无法唯一确认时的人工复核。

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
