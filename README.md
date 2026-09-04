# 名将杀 Agent

面向[名将杀手游]的桌面辅助工具，运行于 PC 端。提供**选将推荐**、**武将数据库查询**、**AI 批量攻略/相性生成**与**屏幕采集 OCR 识别**功能；攻略与相性生成支持 **RAG 官方规则语料增强**（推荐）与经典模式双版本。

**核心功能**

- **选将推荐** — 2 列 × 4 行卡片，ADB 截图或本地图片导入后 OCR 识别武将，展示相性、历史单将胜率与推荐指数
- **武将资料库** — 武将列表搜索/势力筛选、技能/攻略/相性详情查看与编辑，卡牌图鉴只读浏览
- **AI 攻略/相性生成** — DeepSeek API 或浏览器自动化批量生成，默认 RAG 语料增强，可切换经典模式
- **屏幕采集与 OCR** — MuMu ADB 截图 + OpenCV 模板匹配 + PaddleOCR 识别 + 持续轮询
- **知识库维护** — RAG 语料状态/元规则 T0 文档/专属牌·点数·装备·分类数据源本地可视化维护 + 索引精化（LLM 建议 + 人工补全索引字段，已下沉业务层）
- **公告更新监控** — 拉取官方公告 + 百科逐武将 diff，仅武将相关且 diff 确认后提示可更新

---

## 快速开始

### 1. 环境准备

```bash
conda env create -f environment.yml
conda activate myenv

# 浏览器模式需额外安装 Playwright
pip install playwright && playwright install msedge
```

### 2. 运行测试

```bash
python -m ruff check src tests          # 开发环境与 CI 统一 Ruff 0.12.0
python -m pytest --collect-only -q      # 查看用例数（以实际输出为准）
python -m pytest tests/ -v
```

### 3. 启动桌面应用

```bash
python -m src.main
```

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

# 浏览器模式
python -m src.scraper.ai_batch --guide --browser
python -m src.scraper.ai_batch --synergy --browser

# 预览成本（仅 API 模式）
python -m src.scraper.ai_batch --dry-run --guide
```

### 6. RAG 语料维护

```bash
python -m src.scraper.ai_batch --guide --no-rag             # 禁用 RAG 增强
python -m src.scraper.ai_batch --rebuild-rag-index          # 重建向量索引
python -m src.scripts.maintain_rag --force --build-index    # 一键重建语料+索引
python -m src.scripts.rag_audit                             # 查看人工补充清单
```

维护脚本（`maintain_rag.py` / `rag_audit.py` / `build_*.py`）已收编到 `src/scripts/`，以 `python -m src.scripts.<脚本名>` 运行，全部本地执行。元规则 T0 文档维护脚本：`audit_rule_doc.py` / `sync_rule_stats.py` / `propose_rule_changes.py` / `apply_rule_proposal.py` / `eval_rule_faqs.py`，均可在应用内「知识库维护 → 元规则维护」可视化操作。

---

## 目录结构

```
test_project/
├── src/
│   ├── main.py                 # 应用入口
│   ├── config/                 # 配置（env.py / logging_config.py）
│   ├── data/                   # 数据模型 + Manager + JSON 持久化 + RAG 源数据仓储
│   ├── scraper/                # 官网爬虫（official_source/）+ AI 批量生成（ai/）
│   ├── business/               # 业务服务（QProcess/ADB/OCR 编排 + 元规则纯函数）
│   ├── capture/                # ADB 截图与设备探测
│   ├── ocr/                    # 模板匹配 + PaddleOCR + 名称纠错
│   ├── rag/                    # 知识库：向量索引与混合检索基础设施
│   ├── scripts/                # 语料构建与维护脚本（build_*_corpus / maintain_rag / 元规则 CLI）
│   └── ui/                     # PySide6 界面（app/library/recommendation/match/maintenance/...）
├── data/                       # JSON 数据 + RAG 语料/索引 + 官方榜单 CSV
├── images/                     # 武将头像（从官网下载）
├── templates/                  # OCR 模板截图
├── tests/                      # 测试用例
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
│  PySide6 主窗口、对话框、推荐面板、武将浏览器、知识库维护     │
│  信号连接 → 业务服务 → 子进程 → 数据刷新                     │
├────────────────────────────────────────────────────────────┤
│  业务服务层 (src/business/)                                 │
│  QProcess 子进程管理、ADB 截图编排、OCR 轮询控制             │
│  无 UI 引用，通过 Qt Signal 通信                             │
├────────────────────────────────────────────────────────────┤
│  采集层 (src/scraper/ + src/capture/ + src/ocr/)           │
│  官网爬虫 / AI 生成 / ADB 截图 / 模板匹配 / PaddleOCR        │
├────────────────────────────────────────────────────────────┤
│  数据层 (src/data/)                                         │
│  Pydantic 模型 + DataFacade + JSON 持久化                   │
└────────────────────────────────────────────────────────────┘
```

### 数据流

```
武将采集   官网 JS chunk → 清洗 → Pydantic 校验 → data/heroes.json + 头像
AI 生成    武将数据 + Prompt(+RAG语料) → DeepSeek → JSON 提取 → 校验 → data/guides.json / synergies.json
屏幕识别   ADB 截图 → 模板匹配过滤 → PaddleOCR → 名称纠错 → 推荐面板/对局攻略
```

### 运行时边界

- QProcess stdout 以字节缓冲保留未完成行，只对完整换行做 UTF-8 解码与进度解析；`_dispatch_stdout_line` 同步按 `[i/N] 名字 FAIL` 收集失败项到 `failed_items`，出错弹窗据此列出失败清单。
- AI 子进程（`subprocess.ai`）同样只设 `MJS_QPROCESS_CHILD=1` 走 stdout/stderr 转发，不做子进程直写；失败原因由父进程 `scraper/ai_generation.log` handler 的 `keep_debug=True` 保留（级别固定 DEBUG、不跟随用户级别）——即使 root level≥WARNING 时 429/length/JSON 失败原因也不丢；API 限流退避时输出 `[重试]` 行，进度窗口显示"重试中"。
- AI 生成每累计 10 条已校验成功结果原子提交正式 JSON，失败项保留对应旧数据。

### 双模式 AI 生成

```
API 模式 (默认)    → AIBatchGenerator → httpx → DeepSeek API
浏览器模式 (--browser) → PlaywrightGenerator → Playwright + Edge → chat.deepseek.com
```

- **API 模式**：速度快、支持 Token 统计与费用估算、需要付费 API Key。输出上限默认 16384 token（可按供应商语义经 `MAX_OUTPUT_TOKENS` 上调）；正文被"思考过程耗尽输出额度"截断时自动重试；每次调用记录 reasoning/content token 拆分用于定位思考挤占正文预算导致的截断；限流退避重试时进度窗口显示"重试中"。
- **浏览器模式**：免费、无需 API Key、速度较慢、不支持 Token 统计。
- 两种模式 JSON 输出格式一致，差异仅在后端传输方式。

### 配置加载优先级

```
启用 API 档案（api_profiles.json） > config.env > 环境变量 > 默认值
```

任务用唯一启用档案三件套；无启用档案时回退 `config.env` → 环境变量 → 默认值。环境变量作为脚本/CI 注入 Key 的最后兜底长期保留。

---

## 核心功能

- **选将推荐**：固定 2×4 卡片，支持识别模拟器画面或从本地图片导入；卡片展示头像、推荐指数、最佳搭档与相性摘要、历史单将胜率（前三 TOP 徽章）。OCR 待确认名称可在候选白名单内人工确认。轮询模式定时检测武将选择页自动填充。
- **对局攻略**：42/58 分割的 2v2 阵容核对与临场攻略工作台；OCR 导入后按"我方/敌方/未定"分组，确认阵容后展示总览、我方打法、对抗敌方与单将详情。
- **武将资料库**：左侧列表搜索+势力筛选，右侧三 Tab（武将信息/攻略指南/武将相性）；支持武将、攻略、相性的编辑与删除（备份+原子写入，失败恢复原数据）；卡牌图鉴只读浏览与版本调整维护。
- **AI 攻略/相性生成**：全量/增量/指定三种范围；攻略指定获取支持按"未生成/待更新/已有攻略"筛选；相性支持选定武将×全体与 2~8 武将两两配对。生成失败时弹窗详情列出失败武将/相性对清单。
- **屏幕采集与 OCR**：模板匹配作前置过滤（<50ms），命中后执行 PaddleOCR；轮询全程内存处理不写磁盘。模板与 ROI 按参考分辨率自适应缩放。
- **知识库维护**：语料状态（8 任务+审计跳转）、元规则 T0 文档维护（audit/差异/提案/疑难）、专属牌/卡牌点数/装备属性/武将分类四个数据源页签、索引精化（LLM 建议+人工补全 timing/trigger_condition/keywords/related）。
- **官方榜单导入**：2v2 胜率/出场与武将放逐榜图片导入，按视觉行 OCR 并原子覆盖 CSV；名称歧义时按词表候选+逐字+受限繁体兜底，未确认写入待复核。
- **公告监控**：仅 `【新增武将】/【武将调整】` 章节相关公告提醒；百科逐武将 diff 确认后才提示"可更新"，支持指定获取+增量精准更新。

---

## 配置

`config.env`（已 gitignore）管理标量运行参数：

```env
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
MUMU_OCR_USE_GPU=false
MUMU_OCR_CPU_THREADS=6
RAG_ENABLED=true
RAG_TOP_K=12
RAG_PROMPT_CHARS=6000
```

**多 API 档案**（`config/api_profiles.json`，已 gitignore）：支持多供应商/多账号（`deepseek` / `openai` / `ollama` / `openai-compatible`），同时只允许一个启用档案；首次启动若存在旧 `DEEPSEEK_*` 三件套自动迁移为 `deepseek-main` 档案。势力配色经「配置 → 势力配色」可视化编辑。

定价参考：输入 CNY 3/百万 tokens，输出 CNY 6/百万 tokens（deepseek-v4-flash，缓存未命中）。

---

## 外部依赖

| 依赖 | 用途 |
|------|------|
| PySide6 | 桌面 UI 框架 |
| pydantic | 数据模型与校验 |
| httpx | DeepSeek API 请求（API 模式） |
| playwright | 浏览器自动化（浏览器模式） |
| mistune | Markdown → HTML 渲染 |
| paddlepaddle / paddleocr | OCR 识别引擎 |
| opencv-python | 模板匹配 + 图像预处理 |
| pillow (PIL) | 图像处理 |
| chromadb + sentence-transformers | RAG 向量检索（bge-small-zh-v1.5） |
| pytest | 测试框架 |

---

## 日志系统

统一配置在 `src/config/logging_config.py`，按模块分文件 + 10MB 轮转保留 5 份。由桌面应用启动的 QProcess 子进程统一设 `MJS_QPROCESS_CHILD=1` 后只通过 stdout/stderr 交给主进程统一记录（避免多进程轮转竞争）；AI 生成日志由 `scraper/ai_generation.log` 承载（`keep_debug=True`，handler 级别固定 DEBUG），另有 `debug.log` 跨模块全量留底。AI 日志只记录任务、长度、字段、用量与错误摘要，不记录 Prompt、回复正文或认证信息。

```
logs/
├── app.log                  # UI 与数据加载
├── scraper/{official,ai_generation}.log
├── business/{fetching,emulator,recognition,business}.log
├── data/ ocr/ capture/      # 各模块同名日志
└── subprocess/unclassified.log
```

---

## 文档导航

| 文档 | 内容 |
|------|------|
| [docs/project_doc.md](docs/project_doc.md) | 完整项目细节与业务处理逻辑 |
| [docs/code_desc/](docs/code_desc/) | 按模块的职责/核心逻辑/接口/关键代码（9 模块 + 总览，知识库 RAG 为独立模块） |
| [docs/call_graph/](docs/call_graph/) | 各核心功能函数调用链路（9 个调用图） |
| [docs/spec/](docs/spec/) | 设计规格文档 |
| [docs/prompts/](docs/prompts/) | AI 攻略/相性生成 Prompt 模板 |
| [docs/待开发功能记录.md](docs/待开发功能记录.md) | 条件触发/暂不排期的功能规划与已实施 OCR 加速方案 |

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
