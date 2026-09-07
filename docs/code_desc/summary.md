# 名将杀 Agent — 项目总览

> 文档日期：2026-09-07（基线 `6cbe8b6`）
> 项目路径：`G:\py_savepoint\test_project`  
> 远程仓库：`gitee.com:chen-xianghao920/test_project.git`

## 项目简介

名将杀 Agent 是一款面向[名将杀手游](https://mjs.ztgame.com/)的桌面辅助工具。它提供武将数据库查询、AI 批量攻略/相性生成、武将相性分析、实时屏幕采集与 OCR 武将识别、RAG 语料知识库维护等功能，帮助玩家在游戏中快速决策。RAG 语料维护含索引精化工作台，将 LLM 建议编排、清单状态管理与持久化写回下沉为纯业务层，与 UI 解耦。

## 核心功能

- **资料库浏览** — 在”武将资料”中查询武将详情、技能和攻略；在”卡牌图鉴”中只读浏览官方卡牌及维护独立的效果配置
- **选将推荐** — 4×2 网格展示推荐武将，集成相性评分、胜率排名与 OCR 截图导入
- **对局攻略** — 2×2 展示四名武将，支持 ADB/本地图片导入并加载 2v2 胜率
- **AI 攻略生成** — 通过 DeepSeek API 或浏览器自动化批量生成武将攻略，默认 RAG 官方规则语料增强，可切换经典模式（无 RAG 注入）；API 模式支持扩展额度与思考重试
- **AI 相性评分** — 全量/指定武将的相性评分，支持 2~8 武将两两配对；RAG 注入双方武将语料块与规则/FAQ/卡牌跨类块，支持 RAG 增强/经典双版本
- **屏幕采集与 OCR** — 通过 ADB 连接模拟器截图，OpenCV 模板匹配 + PaddleOCR 识别武将名
- **实时轮询** — 统一截图后独立检测武将选择页和对局攻略页，分别维护任务激活状态与冷却时间
- **官方数据导入** — 可独立或同时导入 2v2/武将放逐榜单图片；按表格行写入三份 CSV，显示 OCR 进度，并以词表候选、逐字补识别和待复核保证名称可靠性
- **公告更新监控** — 手动检查官方公告，仅对 `【新增武将】/【武将调整】` 章节相关公告提醒；百科逐武将 diff 确认后才提示”可更新”，并提供”指定获取+增量”一键精准更新；更新流程引入阶段令牌与本地回查防止跨阶段误消费
- **知识库维护（RAG）** — 本地 RAG 语料维护工作台，布局为重排的「左栏维护对象导航 + 右侧数据源工作区 + 底部折叠执行日志」：左栏 10 项（5 个可编辑数据源 专属牌/卡牌点数/装备属性/武将分类/元规则母本 + 5 个只读语料，状态点对齐其语料任务状态）；支持单项 `--only` / 全部 / 加索引三级重建、保存后左栏状态点即时变「待重建」并可就地重建、结构化审计提示并支持跳转定位、10 个语料任务状态常驻可见；源数据已从 xlsx 迁移为 JSON（`data/special_cards.json` / `data/card_points.json` / `data/equip_attrs.json`，xlsx 归档 `data/archive/`）
- **元规则 T0 文档维护** — 以 `docs/元规则整理-完整版.md` 为规则知识库母本（只增不删、机器校验），提供文档校验（audit）、数据段差异同步（sync）、变更提案起草/合入（propose/apply）与疑难登记（pending）完整工作流，并在「知识库维护 → 元规则母本」维护对象（内嵌四个子页签）可视化操作
- **索引精化** — RAG 语料索引字段（timing / trigger_condition / keywords / related）精化工作台，架构下沉为三层纯业务代码：`refinement_service.py`（纯函数：清单扫描/LLM 建议生成/磁盘读写）+ `RefinementSession`（纯 Python 状态层：pending/curated/normal 三池清单、磁盘基线与 LLM 基线双基线、行状态 pending/suggested/modified/refined/generated）+ `SuggestController`（QObject，LLM 线程编排，SuggestWorker 批量建议，含 LIVE_WORKERS 全局僵尸防护列表防止 dialog 销毁后 QThread 运行中析构）

## 技术栈

| 层次 | 技术 |
|------|------|
| 桌面 UI | PySide6（Qt for Python） |
| 数据模型 | Pydantic v2（数据校验与序列化） |
| AI 生成 | httpx（DeepSeek API 同步请求）/ Playwright（浏览器自动化） |
| RAG 检索 | ChromaDB + sentence-transformers（bge-small-zh-v1.5 本地嵌入）+ 关键词 RRF 混合检索 |
| 屏幕采集 | ADB（Android Debug Bridge）exec-out 截图 |
| 图像处理 | OpenCV（模板匹配、表格横线检测）、Pillow（图像格式转换） |
| OCR 识别 | PaddleOCR + 编辑距离矫正 + 汉字特征评分（`unihan_etl` / `cnradical` / `pypinyin`，365 字缓存） |
| 数据持久化 | JSON + CSV 文件（原子写入，无数据库依赖） |
| 测试与静态检查 | pytest 9.0.3（100 文件 / 1129 个 `test_*` 函数，CI `-n auto --timeout=60`）+ Ruff 0.12.0（`F` / `T201` / `I` / `B905`） |
| 异步通信 | QProcess（子进程管理）+ Qt Signal/Slot |

## 整体目录结构

```
test_project/
├── src/
│   ├── main.py                  # 应用入口
│   ├── config/                  # 配置管理（.env 解析、日志配置）
│   ├── data/                    # 数据模型与数据管理层（含 hero_timeline 武将变更时间轴）
│   ├── scraper/                 # 爬虫与 AI 批量生成层
│   ├── rag/                     # RAG 向量索引与混合检索基础设施（ChromaDB + bge-small-zh + 关键词 RRF）
│   ├── scripts/                 # 语料构建与维护脚本（build_*_corpus / maintain_rag / import_hero_adjustments / 元规则维护 CLI）
│   ├── business/                # 业务服务层（QProcess、OCR/官方榜单导入编排、公告与巅峰赛业务）
│   ├── capture/                 # 屏幕采集层（ADB 连接、截图、MuMu 实例探测）
│   ├── ocr/                     # OCR 识别层（模板匹配 + PaddleOCR + 卡位检测）
│   └── ui/                      # PySide6 用户界面层（app / configuration / data_admin / generation / library / match / maintenance / recommendation / shared）
├── data/                        # 数据文件（JSON + 2v2 胜率/出场、放逐 CSV；RAG 源数据 JSON 与归档 archive/）
├── images/                      # 武将头像（PNG）
├── templates/                   # OCR 模板截图
├── screenshots/                 # 手动截图导出目录
├── screenshot_data/             # OCR 识别结果缓存
├── logs/                        # 日志文件
├── tests/                       # 测试用例
├── docs/                        # 文档
├── config.env                   # 用户配置（已 gitignore）
├── CLAUDE.md                    # Claude Code 上下文
├── README.md                    # 项目说明文档
└── environment.yml              # Conda 环境定义
```

## 四层架构概览

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  UI 层 (src/ui/)                                                             │
│  PySide6 窗口、对话框、推荐面板、武将浏览器、对局攻略页                         │
│  知识库维护工作台（含元规则维护与索引精化对话框）                                │
│  索引精化对话框只负责渲染与交互确认，清单归属、基线判定与写盘全部经业务层完成     │
│  信号连接 → 业务服务 → 子进程 → 数据刷新                                      │
├──────────────────────────────────────────────────────────────────────────────┤
│  业务服务层 (src/business/)                                                   │
│  QProcess 子进程管理、ADB 截图编排、OCR 轮询控制                                │
│  RAG 索引精化：refinement_service（纯函数）+ RefinementSession（三池状态）     │
│  + SuggestController（LLM 线程编排，僵尸防护）                                │
│  元规则：rule_doc_service（纯函数）+ audit_service + hero_brief + task_defs  │
│  无 UI 引用，通过 Signal 通信                                                 │
├──────────────────────────────────────────────────────────────────────────────┤
│  采集层 (src/scraper/ + src/capture/ + src/ocr/)                             │
│  官网 JS 字符级状态机解析（extract_js_array）/ AI 生成 / ADB 截图               │
│  模板匹配 / PaddleOCR / 公告 API 与 HTML 回退                                  │
├──────────────────────────────────────────────────────────────────────────────┤
│  数据层 (src/data/)                                                           │
│  Pydantic 模型 + DataFacade + JSON 持久化                                     │
│  ComboManager 落盘按 (-rating, hero1_id, hero2_id) 稳定排序                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

上图为**目录层视图**，描述的是代码物理位置。知识库（RAG）是一个跨目录的纵向功能：`src/rag/` 与 `src/scripts/` 是独立的 RAG 专用目录，`src/business/rag/` 与 `src/ui/maintenance/` 则物理嵌套在 business 与 ui 之下。因此知识库在文档中按**单一模块**整体叙述（见 `./module_rag.md`），而不是拆散到各层里。阅读上图时，标注 RAG 的两层行只是说明代码位置，职责细节请转到知识库模块文档。

## 子模块文档索引

| # | 模块 | 目录 | 主要职责 |
|---|------|------|---------|
| 1 | [应用入口与配置](./module_config.md) | `src/main.py` + `src/config/` | 应用启动、环境配置、日志初始化 |
| 2 | [数据模型与数据管理](./module_data.md) | `src/data/` | Pydantic 模型定义、CRUD 操作、JSON 持久化（含 RAG 源数据仓储、ComboManager 稳定排序落盘、**武将变更时间轴 `hero_timeline`**） |
| 3 | [爬虫与数据采集](./module_scraper.md) | `src/scraper/official_source/` | 官网 JS chunk 字符级状态机解析、数据清洗、头像下载、公告采集与百科 diff |
| 4 | [AI 批量生成](./module_ai_batch.md) | `src/scraper/ai/` | AI 攻略/相性生成、JSON 提取、双模式生成器 |
| 5 | [业务服务层](./module_business.md) | `src/business/` | QProcess 子进程管理、服务编排、官方榜单图片导入、公告更新检查、**选将/巅峰赛/对局攻略三板块共享一次截图的轮询串联**、巅峰赛识别编排与禁选建议 |
| 6 | [知识库（RAG）与元规则维护](./module_rag.md) | `src/rag/` + `src/business/rag/` + `src/ui/maintenance/` + `src/scripts/`（语料与维护脚本） | 语料构建与 ODS/DWD/mart 分层（10 任务 / 12 语料文件 / 2090 块）、向量索引与混合检索、RAG 注入 AI 生成、**武将变更时间轴驱动的语料块版本戳与默认只召当前版本**、索引精化三层架构、元规则 T0 文档维护、数据源编辑与审计 |
| 7 | [屏幕采集与 OCR](./module_capture_ocr.md) | `src/capture/` + `src/ocr/` | ADB 截图与 MuMu 实例探测（`probe_all_devices*` / `probe_running_devices`）、模板匹配、PaddleOCR 识别、官方榜单版式解析 |
| 8 | [UI 界面层](./module_ui.md) | `src/ui/` | 主窗口（AppServices 组合根 + StatusChips）、对话框体系、推荐面板、武将浏览器、巅峰赛选将面板、`MasterDetailPane` 主从列表骨架与实战配队三入口 |
| 9 | [巅峰赛与实战配队](./module_peak_combos.md) | `src/ui/match/peak_*` + `src/business/analysis/peak_ban_advice.py` + `src/business/recognition/peak_select_watcher.py` + `src/data/combo_*` + `src/ocr/card_grid_detector.py` | 巅峰赛（2v2）选将实时识别循环（**会话制互斥 + 会话世代校验 + 候选面板持续刷新治理**）、禁选建议象限判定、实战配队（combos）数据管理、座次解析与配队导入 |

## 本轮文档校准（2026-09-07）

本次对全部 9 个模块文档与 10 份调用图做了与代码逐项核对，除补充新增能力外，修正了以下已失效描述：

**新增能力**
- 武将变更时间轴（`src/data/hero_timeline.py` + `src/scripts/import_hero_adjustments.py`）：公告 diff 落地 `data/mjs_adjustments.json`，语料块打 `as_of` / `is_current` 版本戳，检索层默认只召当前版本
- 巅峰赛识别会话治理：会话制互斥（`start` 即时挂起、每拍幂等重挂、`stop` 恢复全部）、会话世代四处校验、`carry_over_resolutions` 按内容沿用人工确认、`board_exited` 衔接对局攻略页
- 三板块共享一次截图：`OcrService.poll_tick` → `PollCoordinator` 一次截图派生三条判定路径，`hero_selection` 单拍最多消费一次
- 知识库维护工作台布局重排为左栏 10 项导航 + 右侧工作区 + 底部折叠执行日志；语料与维护脚本统一接入 `install_crash_logger` / `get_script_logger`
- UI 公共组件：`MasterDetailPane`（资料库三面板统一接入）、`AppServices` 组合根、`StatusChips`、`CaptureRequestLock`、`run_edit_dialog()`
- 多 API 档案（`config/api_profiles.json`，四供应商、启用互斥、首启自动迁移）与 `config/model_pricing.json`

**修正的失效描述**
- `PollCoordinator` 类名与官方榜单导入机制（共享 FIFO 队列 + `OcrService._import_busy` 互斥，非独占 worker）
- 巅峰赛禁选建议阈值与象限（`HOT_PICK_RANK_MAX=50` / `STRONG_WIN_RATE_MIN=50.0`，仅 `ban_first` / `hot_pick` 两种标签）
- 卡位检测量化步长（位置 8px / 尺寸 16px）、字符缓存规模（365 字）、默认名称 ROI（50×145）
- MuMu 探测 API（`probe_mumu_port()` 已不存在，改 `probe_running_devices()` 等）
- OCR 启动预热语义（启动画面阶段阻塞预热，`wait_ocr_warmup(timeout_ms=120_000)`，非"首次任务延迟加载"）
- 语料任务数（10 个）、语料块总数（2090）、`scripts/*` → `src/scripts/*` 路径与 `.rule_doc_snapshot.json` 位于项目根
- 日志级别分配反转（root 下限 WARNING + `src`/`subprocess` 恒定 DEBUG）、`rag/rag.log` 与 `debug.log`
- 配置键补齐（`MAX_OUTPUT_TOKENS`、`MUMU_OCR_AUTO_SWITCH_TAB`、`RECOMMENDATION_*` 四键等）
- `DataManager` 签名（`add(item, key)` / `update(item, key)`）与 `clear_all` / `snapshot_items` / `restore_items`

**已知但未处理的死代码**（按"发现只提醒、不擅自删除"原则保留）
- `src/business/maintenance/classification_suggest.py` 与 `classification_suggest_worker.py`：武将 LLM 建议归类功能，当前 `src/` 内零调用
- `src/config/env.py::list_api_profiles()`：当前仅测试消费
