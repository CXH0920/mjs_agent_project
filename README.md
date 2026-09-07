# 名将杀 Agent

面向[名将杀手游](https://mjs.ztgame.com/)的桌面辅助工具，运行于 PC 端。提供**选将推荐**、**武将资料库**、**对局攻略**、**巅峰赛选将**、**AI 批量攻略/相性生成**与**屏幕采集 OCR 识别**功能；攻略与相性生成支持 **RAG 官方规则语料增强**（推荐）与经典模式双版本。

**核心功能**

- **选将推荐** — 2 列 × 4 行卡片，ADB 截图或本地图片导入后 OCR 识别武将，展示相性、历史单将胜率与推荐指数；实战配队横条与卡片角标共用同一数据源
- **武将资料库** — 武将列表搜索/势力筛选、技能/攻略/相性详情查看与编辑，卡牌图鉴只读浏览；卡牌/专属牌/武将分类三面板统一接入 `MasterDetailPane` 主从骨架
- **AI 攻略/相性生成** — 多供应商 LLM API 或浏览器自动化批量生成，默认 RAG 语料增强，可切换经典模式
- **屏幕采集与 OCR** — MuMu ADB 截图 + OpenCV 模板匹配 + PaddleOCR 识别 + 持续轮询；选将、巅峰赛、对局攻略三板块共享一次截图
- **知识库维护** — RAG 语料状态/元规则 T0 母本/专属牌·点数·装备·分类数据源本地可视化维护 + 索引精化（LLM 建议 + 人工补全索引字段，已下沉业务层）
- **公告更新监控** — 拉取官方公告 + 百科逐武将 diff，仅武将相关且 diff 确认后提示可更新，并落地武将变更时间轴驱动语料版本戳

---

## 快速开始

### 1. 环境准备

```bash
conda env create -f environment.yml
conda activate myenv

# 浏览器模式需额外安装 Edge 浏览器内核（playwright 包已在 environment.yml 中）
playwright install msedge

# CUDA 11.8 / cuDNN 8.x 运行时 DLL 需复制到 paddle/libs（Windows + paddlepaddle-gpu）
```

### 2. 运行测试

```bash
python -m ruff check src tests          # Ruff 0.12.0，规则 F / T201 / I / B905
python -m pytest --collect-only -q      # 查看用例数（以实际输出为准）
python -m pytest tests/ -v
```

> 本机 `Temp` 目录访问受限时，需加 `--basetemp=.tmp_test/pytest-tmp`。
> CI 执行 `pytest -q -n auto --timeout=60 --timeout-method=thread`，并收集 `logs/pytest-timeout-*.log`。

### 3. 启动桌面应用

```bash
python -m src.main
```

启动画面期间阻塞预热 OCR 模型（上限 120 秒），预热完成后才显示主窗口。

### 4. 数据采集

```bash
python -m src.scraper.official                              # 全量采集（自动下载头像）
python -m src.scraper.official --skip-images                # 跳过头像
python -m src.scraper.incremental --incremental             # 增量采集
python -m src.scraper.incremental --hero 诸葛亮,关羽        # 按名称采集
python -m src.scraper.incremental --hero-id 52,114          # 按 ID 采集
```

> 公告更新检查无 CLI，在应用内「数据 > 检查公告更新」手动触发。

### 5. AI 批量生成

```bash
# API 模式（需配置 API Key）
python -m src.scraper.ai_batch --guide                      # 生成攻略
python -m src.scraper.ai_batch --synergy                    # 全量相性
python -m src.scraper.ai_batch --guide --update             # 仅重新生成已有攻略

# 指定范围
python -m src.scraper.ai_batch --synergy-pair 诸葛亮,关羽   # 指定一对
python -m src.scraper.ai_batch --synergy-single 诸葛亮      # 该武将 × 全体
python -m src.scraper.ai_batch --synergy-list 诸葛亮,关羽,张飞  # 名单两两配对

# 浏览器模式
python -m src.scraper.ai_batch --guide --browser
python -m src.scraper.ai_batch --synergy --browser

# 预览成本（仅 API 模式）与阈值过滤
python -m src.scraper.ai_batch --dry-run --guide
python -m src.scraper.ai_batch --synergy --score-threshold 60
```

### 6. RAG 语料维护

```bash
python -m src.scraper.ai_batch --guide --no-rag             # 禁用 RAG 增强
python -m src.scraper.ai_batch --rebuild-rag-index          # 重建向量索引
python -m src.scripts.maintain_rag --force --build-index    # 一键重建语料+索引
python -m src.scripts.rag_audit                             # 查看人工补充清单
python -m src.scripts.import_hero_adjustments --input <json>  # 首次注入武将变更时间轴
```

维护脚本（`maintain_rag.py` / `rag_audit.py` / `build_*_corpus.py`）已收编到 `src/scripts/`，以 `python -m src.scripts.<脚本名>` 运行，全部本地执行。语料与维护脚本统一接入 `install_crash_logger` / `get_script_logger`，日志落 `logs/rag/<脚本名>.log`。元规则 T0 文档维护脚本：`audit_rule_doc.py` / `sync_rule_stats.py` / `propose_rule_changes.py` / `apply_rule_proposal.py` / `eval_rule_faqs.py`，均可在应用内「知识库维护 → 元规则母本」可视化操作。

---

## 目录结构

```
test_project/
├── src/
│   ├── main.py                 # 应用入口（启动画面 + OCR 阻塞预热）
│   ├── config/                 # 配置（env.py / logging_config.py）
│   ├── data/                   # 数据模型 + Manager + JSON 持久化 + RAG 源数据仓储
│   │                           #   + hero_timeline（武将变更时间轴）
│   ├── scraper/                # 官网爬虫（official_source/）+ AI 批量生成（ai/）
│   ├── business/               # 业务服务（QProcess/ADB/OCR 编排 + 分析 + 维护 + RAG 业务）
│   ├── capture/                # ADB 截图与 MuMu 实例探测
│   ├── ocr/                    # 模板匹配 + PaddleOCR + 名称纠错 + 卡位检测
│   ├── rag/                    # 知识库：向量索引与混合检索基础设施
│   ├── scripts/                # 语料构建与维护脚本（build_*_corpus / maintain_rag / 元规则 CLI）
│   └── ui/                     # PySide6 界面（app / configuration / data_admin / generation /
│                               #   library / match / maintenance / recommendation / shared）
├── data/                       # JSON 数据 + RAG 语料/索引 + 官方榜单 CSV
├── images/                     # 武将头像（从官网下载）
├── templates/                  # OCR 模板截图
├── config/                     # api_profiles.json / model_pricing.json / ocr_rois.json / faction_colors.json
├── tests/                      # 测试用例（100 文件 / 1129 个 test_* 函数）
├── docs/                       # 文档（见下方文档导航）
├── config.env                  # 用户配置（已 gitignore）
├── environment.yml             # Conda 环境定义
└── README.md
```

> 逐文件注释与完整结构见 [docs/code_desc/summary.md](docs/code_desc/summary.md)。

---

## 架构

### 四层架构

```
┌────────────────────────────────────────────────────────────┐
│  UI 层 (src/ui/)                                            │
│  PySide6 主窗口（AppServices 组合根 + StatusChips）          │
│  对话框、推荐面板、武将浏览器、巅峰赛选将、知识库维护工作台     │
│  公共骨架 MasterDetailPane / run_edit_dialog / CaptureLock   │
│  信号连接 → 业务服务 → 子进程 → 数据刷新                     │
├────────────────────────────────────────────────────────────┤
│  业务服务层 (src/business/)                                 │
│  QProcess 子进程管理、ADB 截图编排、OCR 轮询控制             │
│  分析（推荐/对局/巅峰赛禁选）、维护、RAG 业务编排             │
│  无 UI 引用，通过 Qt Signal 通信                             │
├────────────────────────────────────────────────────────────┤
│  采集层 (src/scraper/ + src/capture/ + src/ocr/)           │
│  官网爬虫 / AI 生成 / ADB 截图 / 模板匹配 / 卡位检测 / OCR    │
│  RAG 检索基础设施 (src/rag/) + 语料与维护脚本 (src/scripts/)  │
├────────────────────────────────────────────────────────────┤
│  数据层 (src/data/)                                         │
│  Pydantic 模型 + DataFacade + JSON 持久化 + 维护仓储         │
│  武将变更时间轴 (hero_timeline) + 榜单/推荐指数仓储           │
└────────────────────────────────────────────────────────────┘
```

### 数据流

```
武将采集   官网 JS chunk → 字符级状态机解析 → 清洗 → Pydantic 校验 → data/heroes.json + 头像
AI 生成    武将数据 + Prompt(+RAG语料) → LLM → JSON 提取 → 校验 → data/guides.json / synergies.json
屏幕识别   ADB 截图 → 模板匹配过滤 → PaddleOCR → 名称纠错 → 推荐面板/对局攻略
巅峰赛     ADB 截图 → 卡位检测 → 名条 OCR → 候选池/禁选建议/实战配队
公告监控   官方公告 → 章节过滤 → 百科逐武将 diff → 确认 → 精准更新 + 时间轴追加
语料维护   data/*.json 源数据 → build_*_corpus → rag_corpus → chroma 索引 → 生成时注入
```

### 运行时边界

- QProcess stdout 以字节缓冲保留未完成行，只对完整换行做 UTF-8 解码与进度解析；`_dispatch_stdout_line` 同步按 `[i/N] 名字 FAIL` 收集失败项到 `failed_items`，出错弹窗据此列出失败清单。
- AI 子进程（`subprocess.ai`）同样只设 `MJS_QPROCESS_CHILD=1` 走 stdout/stderr 转发，不做子进程直写；失败原因由父进程 `scraper/ai_generation.log` handler 的 `keep_debug=True` 保留（级别固定 DEBUG、不跟随用户级别）——即使 root level≥WARNING 时 429/length/JSON 失败原因也不丢；API 限流退避时输出 `[重试]` 行，进度窗口显示"重试中"。
- AI 生成每累计 10 条已校验成功结果原子提交正式 JSON，失败项保留对应旧数据。
- OCR 全部任务（预热 / 常规识别 / 官方整批导入 / 巅峰赛识别）共享唯一 `OcrWorker` 的 FIFO 队列，互斥由 `OcrService._import_busy` 串行化；轮询全程内存处理不写磁盘。
- 启动阶段 OCR 模型在启动画面期间阻塞预热（`wait_ocr_warmup(timeout_ms=120_000)`），避免 Paddle 初始化持有 GIL 时卡住事件循环。

### 多模式 AI 生成

```
API 模式 (默认)     → AIBatchGenerator → httpx → 多供应商档案（deepseek / openai / ollama / openai-compatible）
浏览器模式 (--browser) → PlaywrightGenerator → Playwright + Edge → chat.deepseek.com
```

- **API 模式**：速度快、支持 Token 统计与费用估算、需要付费 API Key。输出上限默认 16384 token（可按供应商语义经 `MAX_OUTPUT_TOKENS` 上调）；正文被"思考过程耗尽输出额度"截断时自动重试；每次调用记录 reasoning/content token 拆分用于定位思考挤占正文预算导致的截断；429 按 `Retry-After` 钳到 3–30 秒退避；连接类异常先 `close()` 再重建 client；限流退避重试时进度窗口显示"重试中"。
- **浏览器模式**：免费、无需 API Key、速度较慢、不支持 Token 统计。
- 两种模式 JSON 输出格式一致，差异仅在后端传输方式。`thinking` 参数仅 `provider=deepseek` 注入。

### 配置加载优先级

```
启用 API 档案（api_profiles.json） > config.env > 环境变量 > 默认值
```

生成链路经 `resolve_api_config()` 解析：取唯一启用档案三件套；无启用档案时回退 `config.env` → 环境变量 → 默认值。环境变量作为脚本/CI 注入 Key 的最后兜底长期保留。可用档案与启用档案是两个概念——`_usable_profile_config` 只判断"可用"（`enabled` + URL 非空 + 供应商 Key 语义），启用互斥在保存时收敛。价格配置独立存于 `config/model_pricing.json`。

---

## 核心功能

- **选将推荐**：固定 2×4 卡片，支持识别模拟器画面或从本地图片导入；卡片展示头像、推荐指数、最佳搭档与相性摘要、历史单将胜率（前三 TOP 徽章）。OCR 待确认名称可在候选白名单内人工确认。轮询模式定时检测武将选择页自动填充。
- **对局攻略**：42/58 分割的 2v2 阵容核对与临场攻略工作台；OCR 导入后按"我方/敌方/未定"分组，确认阵容后展示总览、我方打法、对抗敌方与单将详情。
- **武将资料库**：左侧列表搜索+势力筛选，右侧三 Tab（武将信息/攻略指南/武将相性）；支持武将、攻略、相性的编辑与删除（备份+原子写入，失败恢复原数据）；卡牌图鉴只读浏览与版本调整维护。
- **AI 攻略/相性生成**：全量/增量/指定三种范围；攻略指定获取支持按"未生成/待更新/已有攻略"筛选；相性支持选定武将×全体与 2~8 武将两两配对。生成失败时弹窗详情列出失败武将/相性对清单。
- **屏幕采集与 OCR**：模板匹配作前置过滤（<50ms），命中后执行 PaddleOCR；轮询全程内存处理不写磁盘。模板与 ROI 按参考分辨率自适应缩放。
- **知识库维护**：语料状态（10 任务 / 12 语料文件 / 2090 块 + 审计跳转）、元规则 T0 母本维护（audit/差异/提案/疑难）、专属牌/卡牌点数/装备属性/武将分类数据源维护、索引精化（LLM 建议+人工补全 timing/trigger_condition/keywords/related）。布局为重排后的「左栏 10 项维护对象导航 + 右侧数据源工作区 + 底部折叠执行日志」。
- **语料版本戳**：公告 diff 落地 `data/mjs_adjustments.json` 武将变更时间轴，RAG 语料块打 `as_of` / `is_current` 戳，检索层默认只召当前版本，过时块带 `staleness_reason` 提示。
- **官方榜单导入**：2v2 胜率/出场与武将放逐榜图片导入，按视觉行 OCR 并原子覆盖 CSV；名称歧义时按词表候选+逐字+受限繁体兜底，未确认写入待复核。
- **公告监控**：仅 `【新增武将】/【武将调整】` 章节相关公告提醒；百科逐武将 diff 确认后才提示"可更新"，支持指定获取+增量精准更新。
- **巅峰赛选将**：2v2 牌面实时识别（内容驱动卡位检测，非固定 ROI），会话制互斥 + 会话世代校验，候选池、禁选建议（出场热度 × 胜率强度象限）与实战配队横条联动。
- **实战配队**：外部导出 JSON 或 UI 手工维护 1228 条配队，座次解析 + position 交叉校验，落盘稳定排序；选将推荐横条与巅峰赛卡片角标共用同一数据源。

---

## 配置

`config.env`（已 gitignore）管理标量运行参数：

```env
# AI 运行参数
REQUESTS_PER_MINUTE=30
HTTP_TIMEOUT=300
MAX_RETRIES=3
MAX_OUTPUT_TOKENS=16384
LOG_LEVEL=INFO
LOG_TO_FILE=true

# 模拟器 (MuMu)
MUMU_ADB_PATH=D:\模拟器\MuMu Player 12\nx_main\adb.exe
MUMU_ADB_PORT=16448
MUMU_OCR_ENABLED=true
MUMU_OCR_POLL_MODE=true
MUMU_OCR_AUTO_SWITCH_TAB=true
MUMU_OCR_POLL_INTERVAL=2
MUMU_OCR_MATCH_THRESHOLD=0.8
MUMU_OCR_USE_GPU=false
MUMU_OCR_CPU_THREADS=6
MUMU_HERO_SELECTION_THRESHOLD=0.8
MUMU_HERO_SELECTION_COOLDOWN=180
MUMU_MATCH_GUIDE_THRESHOLD=0.6

# RAG
RAG_ENABLED=true
RAG_TOP_K=12
RAG_PROMPT_CHARS=6000
RAG_MODEL_DIR=
RAG_PROJECT_DIR=
```

**多 API 档案**（`config/api_profiles.json`，已 gitignore）：支持多供应商/多账号（`deepseek` / `openai` / `ollama` / `openai-compatible`），同时只允许一个启用档案；首次启动若存在旧 `DEEPSEEK_*` 三件套自动迁移为 `deepseek-main` 档案。生成链路经 `resolve_api_config()` 解析（启用档案优先，否则回退 `config.env` → 环境变量 → 默认值）。价格参考来自 `config/model_pricing.json`，势力配色经「配置 → 势力配色」可视化编辑。

定价参考：输入 CNY 3/百万 tokens，输出 CNY 6/百万 tokens（deepseek-v4-flash，缓存未命中）。

---

## 外部依赖

| 依赖 | 用途 |
|------|------|
| PySide6 6.11.1 | 桌面 UI 框架 |
| pydantic 2.13.4 | 数据模型与校验 |
| httpx 0.28.1 | 多供应商 LLM API 请求（API 模式） |
| playwright 1.60.0 | 浏览器自动化（浏览器模式） |
| mistune 3.3.0 | Markdown → HTML 渲染 |
| paddlepaddle 2.6.2 / paddleocr 2.8.1 | OCR 识别引擎 |
| opencv-python 4.11.0.86 | 模板匹配 + 卡位检测 + 图像预处理 |
| pillow 12.3.0 / numpy 1.26.4 | 图像处理 |
| chromadb 1.5.9 + sentence-transformers 5.7.0 | RAG 向量检索（bge-small-zh-v1.5） |
| unihan_etl / cnradical / pypinyin | 汉字特征库（OCR 名称纠错：四角号码、部首、笔画、拼音） |
| pytest 9.0.3 / ruff 0.12.0 | 测试框架与静态检查 |

---

## 日志系统

统一配置在 `src/config/logging_config.py`，按模块分文件 + 10MB 轮转保留 5 份。由桌面应用启动的 QProcess 子进程统一设 `MJS_QPROCESS_CHILD=1` 后只通过 stdout/stderr 交给主进程统一记录（避免多进程轮转竞争）；AI 生成日志由 `scraper/ai_generation.log` 承载（`keep_debug=True`，handler 级别固定 DEBUG），另有 `debug.log` 跨模块全量留底。AI 日志只记录任务、长度、字段、用量与错误摘要，不记录 Prompt、回复正文或认证信息。

**级别分配为反转策略**：`root` 下限 WARNING（高效压制 chromadb / transformers 等第三方库的 INFO/DEBUG），同时 `src` 与 `subprocess` 前缀恒定 DEBUG 全量创建，保证 `debug.log` 留底完整。语料与维护脚本另经 `get_script_logger()` 惰性写入 `logs/rag/<脚本名>.log`。

```
logs/
├── app.log                  # UI 与数据加载
├── scraper/{official,ai_generation}.log
├── business/{fetching,emulator,recognition,business}.log
├── data/ ocr/ capture/      # 各模块同名日志
├── rag/rag.log              # 检索基础设施
├── rag/<script>.log         # 各语料/维护脚本
└── subprocess/unclassified.log
debug.log（与 logs/ 平级）   # 跨模块全量留底
```

---

## 文档导航

| 文档 | 内容 |
|------|------|
| [docs/project_doc.md](docs/project_doc.md) | 完整项目细节与业务处理逻辑（15 章，基线 2026-09-07） |
| [docs/code_desc/](docs/code_desc/) | 按模块的职责/核心逻辑/接口/关键代码（9 模块 + [总览](docs/code_desc/summary.md)，知识库 RAG 为独立模块） |
| [docs/call_graph/](docs/call_graph/) | 各核心功能函数调用链路（10 个调用图） |
| [docs/spec/](docs/spec/) | 设计规格文档 |
| [docs/design/](docs/design/) | 设计决策记录 |
| [docs/prompts/](docs/prompts/) | AI 攻略/相性生成 Prompt 模板 |
| [docs/元规则整理-完整版.md](docs/元规则整理-完整版.md) | 规则知识库 T0 母本（机器校验） |
| [docs/周更操作手册.md](docs/周更操作手册.md) / [docs/待开发功能记录.md](docs/待开发功能记录.md) | 周更流程 / 条件触发与暂不排期功能 |

---

## 开发状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| 一 | 项目脚手架与数据模型 | ✅ 已完成 |
| 二 | 数据采集（官网爬虫 + AI 批量生成） | ✅ 已完成 |
| 三 | PySide6 桌面应用 UI | ✅ 已完成 |
| 四 | 武将相性交互获取 | ✅ 已完成 |
| 五 | 武将头像下载 | ✅ 已完成 |
| 六 | 选将推荐（2×4 卡片 + OCR 导入） | ✅ 已完成 |
| 七 | 浏览器自动化双模式 AI 生成 | ✅ 已完成 |
| 八 | 屏幕采集（ADB + 模板匹配 + PaddleOCR + 轮询） | ✅ 已完成 |
| 九 | 推荐引擎（相性/胜率/OCR 导入） | ✅ 已完成 |
| 十 | 武将与攻略编辑 | ✅ 已完成 |
| 十一 | 相性配对多武将组合（最多 8 武将） | ✅ 已完成 |
| 十二 | 公告监控（百科 diff + 精准更新） | ✅ 已完成 |
| 十三 | 对局攻略工作台（2v2 阵容核对 + 临场攻略） | ✅ 已完成 |
| 十四 | 巅峰赛选将实时识别（卡位检测 + 候选池） | ✅ 已完成 |
| 十五 | 实战配队数据管理（combos + 座次解析 + 导入） | ✅ 已完成 |
| 十六 | RAG 语料知识库（分层语料 + 混合检索 + 注入） | ✅ 已完成 |
| 十七 | 元规则 T0 文档维护工作流（audit/sync/propose/apply） | ✅ 已完成 |
| 十八 | 官方榜单图片导入（双版式 + 复核 + 待复核） | ✅ 已完成 |
| 十九 | 知识库维护工作台（四数据源 + 索引精化三层架构） | ✅ 已完成 |
| 二十 | 多 API 档案（多供应商 + 启用互斥 + 首启迁移） | ✅ 已完成 |
| 二十一 | 武将变更时间轴与语料版本戳（默认只召当前版本） | ✅ 已完成 |
| 二十二 | 巅峰赛识别会话治理与三板块共享一次截图 | ✅ 已完成 |

> 文档基线：2026-09-07（`6cbe8b6`）。测试 100 文件 / 1129 个 `test_*` 函数，Ruff 0.12.0 全通过。
