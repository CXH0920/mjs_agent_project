# 名将杀 Agent — 项目总览

> 文档日期：2026-10-02（基线 `885ea96` + 工作树未提交改动）
> 项目路径：`G:\py_savepoint\test_project`  
> 远程仓库：`gitee.com:chen-xianghao920/test_project.git`

## 项目简介

名将杀 Agent 是一款面向[名将杀手游](https://mjs.ztgame.com/)的桌面辅助工具。它提供武将数据库查询、AI 批量攻略/相性生成、武将相性分析、实时屏幕采集与 OCR 武将识别、RAG 语料知识库维护等功能，帮助玩家在游戏中快速决策。RAG 语料维护含索引精化工作台，将 LLM 建议编排、清单状态管理与持久化写回下沉为纯业务层，与 UI 解耦。

自基线 `624c8c5`（2026-09-15）以来，项目新增 B2 复核模式（PP-OCRv6-small/ONNX 未决槽位候选确认）、白名单治理（错法频次记录 + 人工确认 + 用户层白名单维护）、轮询闲置自动暂停（整帧指纹判闲 + 三路恢复）与合规化改造（免责声明弹窗 + 附加法律条款 + robots.txt 存档）。2026-09-29 后进一步落地 Phase 4 上帝类拆分（六大目标按职责域出仓 14 个新模块）、API 档案域拆分、巅峰赛选将人工确认残留修复（导入前校验、停止清空确认表/禁将基线、牌面在位守卫、同名槽位显性告警）与官网武将数据同步（新增谢灵运/陶渊明）。2026-10-02 完成审计 G8：CaptureService 职责域出仓（官方导入网关 `official_import_gateway` / 图像保存调度 `image_save_scheduler` / OCR 任务协调器 `ocr_task_coordinator` 三模块，670→570 行，UI 门面信号零改动）、`save_results` 落盘 page_type 参数化（不再硬编码 `wujiang_select`）与行数棘轮 500 行 tripwire 落地（12 个 >500 行文件补种入册，超线未登记即测试变红）。

## 核心功能

- **资料库浏览** — 在”武将资料”中查询武将详情、技能和攻略；在”卡牌图鉴”中只读浏览官方卡牌及维护独立的效果配置
- **选将推荐** — 4×2 网格展示推荐武将，集成相性评分、胜率排名与 OCR 截图导入
- **对局攻略** — 2×2 展示四名武将，支持 ADB/本地图片导入并加载胜率榜（按对局链路区分 2v2 / 巅峰赛，`WIN_RATE_MODE_2V2` / `WIN_RATE_MODE_PEAK`）
- **AI 攻略生成** — 通过多供应商 API（DeepSeek / OpenAI / Ollama / OpenAI 兼容档案）或浏览器自动化批量生成武将攻略，默认 RAG 官方规则语料增强，可切换经典模式（无 RAG 注入）；API 模式支持扩展额度与思考重试
- **AI 相性评分** — 全量/指定武将的相性评分，支持 2~8 武将两两配对；RAG 注入双方武将语料块与规则/FAQ/卡牌跨类块，支持 RAG 增强/经典双版本
- **屏幕采集与 OCR** — 通过 ADB 连接模拟器截图，OpenCV 模板匹配 + PaddleOCR 识别武将名
- **实时轮询** — 统一截图后独立检测武将选择页和对局攻略页，分别维护任务激活状态与冷却时间
- **官方数据导入** — 可独立或同时导入 2v2/巅峰赛/武将放逐榜单图片；按表格行写入三份 CSV，显示 OCR 进度，并以词表候选、逐字补识别和待复核保证名称可靠性
- **公告更新监控** — 手动检查官方公告，仅对 `【新增武将】/【武将调整】` 章节相关公告提醒；百科逐武将 diff 确认后才提示”可更新”，并提供”指定获取+增量”一键精准更新；差异条目可条目级加入忽略名单（`baike_ignore.json`，state+hash 定位同一差异，恢复忽略后自动重现），主窗口提供「百科忽略名单管理」入口；更新流程引入阶段令牌与本地回查防止跨阶段误消费
- **卡牌百科变更捕获** — 手动检查官网手牌库与本地卡牌差异，勾选后应用官网文本覆盖本地（`card_amount` 保留）；快照（`card_snapshot.json`）与变更记录（`card_changes.json`）持久化，驱动卡牌精化时效检查
- **知识库维护（RAG）** — 本地 RAG 语料维护工作台，布局为重排的「左栏维护对象导航 + 右侧数据源工作区 + 底部折叠执行日志」：左栏 10 项（5 个可编辑数据源 专属牌/卡牌点数/装备属性/武将分类/元规则母本 + 5 个只读语料，状态点对齐其语料任务状态）；支持单项 `--only` / 全部 / 加索引三级重建、保存后左栏状态点即时变「待重建」并可就地重建、结构化审计提示并支持跳转定位、10 个语料任务状态常驻可见；源数据已从 xlsx 迁移为 JSON（`data/special_cards.json` / `data/card_points.json` / `data/equip_attrs.json`，xlsx 归档 `data/archive/`）
- **元规则 T0 文档维护** — 以 `docs/元规则整理-完整版.md` 为规则知识库母本（只增不删、机器校验），提供文档校验（audit）、数据段差异同步（sync）、变更提案起草/合入（propose/apply）与疑难登记（pending）完整工作流，并在「知识库维护 → 元规则母本」维护对象（内嵌四个子页签）可视化操作
- **索引精化** — RAG 语料索引字段（timing / trigger_condition / keywords / related）精化工作台，架构下沉为三层纯业务代码：`refinement_service.py`（纯函数：清单扫描/LLM 建议生成/磁盘读写）+ `RefinementSession`（纯 Python 状态层：pending/curated/normal 三池清单、磁盘基线与 LLM 基线双基线、行状态 pending/suggested/modified/refined/generated）+ `SuggestController`（QObject，LLM 线程编排，SuggestWorker 批量建议，含 LIVE_WORKERS 全局僵尸防护列表防止 dialog 销毁后 QThread 运行中析构）
- **B2 复核模式** — 对未决识别槽位，使用 PP-OCRv6-small/ONNX 引擎（RapidOCR）做候选内确认；惰性加载+失败熔断，模型缺失不联网下载，设备固定 CPU；`RapidOcrEngine` 包装层将 RapidOCR 结果翻译为 paddleocr 2.x 风格
- **白名单治理** — OCR 未决错法频次记录（`pending_stats.py`，双事件流 + 60 秒节流窗口 + 原子替换写入）+ 用户层白名单维护界面（`whitelist_config_dialog.py`，错法观察清单 A+/A/B/C 分类 + `ocr_confusion_overrides.json` 静态冲突检查 + `reset_ocr_recognizer_cache()` 即时生效）
- **轮询闲置自动暂停** — 整帧降采样指纹（`frame_fingerprint.py`，32×18 灰度 576 字节，MAD 阈值 3.0）+ 轮询协调器（`poll_coordinator.py`，连续 5 分钟无画面变化自动暂停，三路交互恢复）+ 阈值校准工具（`calibrate_idle_threshold.py`）
- **合规化改造** — 免责声明弹窗（`disclaimer_dialog.py` + `disclaimer_state.py`，版本感知仅条款更新时重新确认）+ 附加法律条款（LICENSE 第 8 条授权终止）+ robots.txt 存档（`logs/robots_cache/`）+ 架构防火墙声明（代码不含 ADB 输入能力）

## 技术栈

| 层次 | 技术 |
|------|------|
| 桌面 UI | PySide6（Qt for Python） |
| 数据模型 | Pydantic v2（数据校验与序列化） |
| AI 生成 | httpx（多供应商 OpenAI 风格 API 同步请求）/ Playwright（浏览器自动化） |
| RAG 检索 | ChromaDB + sentence-transformers（bge-small-zh-v1.5 本地嵌入）+ 关键词 RRF 混合检索 |
| 屏幕采集 | ADB（Android Debug Bridge）exec-out 截图 |
| 图像处理 | OpenCV（模板匹配、表格横线检测）、Pillow（图像格式转换） |
| OCR 识别 | PaddleOCR + 编辑距离矫正 + 汉字特征评分（`unihan_etl` / `cnradical` / `pypinyin`，424 字特征缓存）+ B2 复核引擎（RapidOCR 3.9.2 / PP-OCRv6-small/ONNX） |
| 数据持久化 | JSON + CSV 文件（原子写入，无数据库依赖） |
| 测试与静态检查 | pytest 9.0.3（112 文件 / 1350 个 `test_*` 函数，CI `-n auto --timeout=60`）+ Ruff 0.12.0（`F` / `T201` / `I` / `B905`）+ vulture / pyright（CI report-only） |
| 异步通信 | QProcess（子进程管理）+ Qt Signal/Slot |

## 整体目录结构

```
test_project/
├── src/
│   ├── main.py                  # 应用入口
│   ├── config/                  # 配置管理（.env 解析、日志配置、disclaimer_state 免责声明状态）
│   ├── data/                    # 数据模型与数据管理层（DataFacade 门面 + manager/issues 解环拆分；含 hero_timeline 武将变更时间轴、card_sync_store 卡牌百科快照、baike_ignore_store 百科忽略名单）
│   ├── scraper/                 # 爬虫与 AI 批量生成层（含 card_baike 卡牌百科抓取清洗）
│   ├── rag/                     # RAG 向量索引与混合检索基础设施（ChromaDB + bge-small-zh + 关键词 RRF）
│   ├── scripts/                 # 语料构建与维护脚本（build_*_corpus / maintain_rag / import_hero_adjustments / 元规则维护 CLI）
│   │                           #   + ocr_baseline（OCR 回归基线工具）/ calibrate_idle_threshold（闲置阈值校准）
│   ├── business/                # 业务服务层（QProcess、OCR/官方榜单导入编排、公告与巅峰赛业务、卡牌百科同步）
│   │                           #   + emulator 协作模块（capture_service 门面 + ocr_task_coordinator / official_import_gateway / image_save_scheduler，审计 G8）
│   │                           #   + pending_stats（OCR 未决错法频次与人工确认记录）
│   ├── capture/                 # 屏幕采集层（ADB 连接、截图、MuMu 实例探测）
│   ├── ocr/                     # OCR 识别层（模板匹配 + PaddleOCR + 卡位检测 + paddle_loader B2 复核引擎）
│   └── ui/                      # PySide6 用户界面层（app / configuration / data_admin / generation / library / match / maintenance / recommendation / shared）
│                               #   + 主窗口协调器与自足组件（app_services / status_chips / progress_reporter / poll_coordinator / announcement_update_coordinator）
│                               #   + whitelist_config_dialog（白名单配置）/ baike_ignore_manager_dialog（百科忽略名单）/ frame_fingerprint（闲置指纹）
│                               #   + disclaimer_dialog（免责声明对话框）
├── data/                        # 数据文件（JSON + 2v2/巅峰赛胜率出场、放逐 CSV；RAG 源数据 JSON 与归档 archive/；含 card_snapshot.json / card_changes.json 卡牌百科快照与 baike_ignore.json 忽略名单）
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
│  主窗口协调器与自足组件（AppServices 组合根 / StatusChips / ProgressReporter    │
│  / PollCoordinator / AnnouncementUpdateCoordinator）                          │
│  白名单配置界面（whitelist_config_dialog）、轮询闲置检测与协调                    │
│  （frame_fingerprint / poll_coordinator）、百科忽略名单管理、免责声明对话框      │
│  索引精化对话框只负责渲染与交互确认，清单归属、基线判定与写盘全部经业务层完成     │
│  UI 层数据 import 白名单：UI 不构造数据层对象（tests/test_architecture 守护）    │
│  信号连接 → 业务服务 → 子进程 → 数据刷新                                      │
├──────────────────────────────────────────────────────────────────────────────┤
│  业务服务层 (src/business/)                                                   │
│  QProcess 子进程管理、ADB 截图编排、OCR 轮询控制（含闲置暂停治理）              │
│  RAG 索引精化：refinement_service（纯函数）+ RefinementSession（三池状态）     │
│  + SuggestController（LLM 线程编排，僵尸防护）                                │
│  卡牌百科：CardSyncService（手动检查官网手牌库差异，勾选后应用更新）              │
│  白名单：pending_stats（未决错法频次记录与人工确认答案收集）                    │
│  元规则：rule_doc_service（纯函数）+ audit_service + hero_brief + task_defs  │
│  无 UI 引用，通过 Signal 通信                                                 │
├──────────────────────────────────────────────────────────────────────────────┤
│  采集层 (src/scraper/ + src/capture/ + src/ocr/)                             │
│  官网 JS 字符级状态机解析（extract_js_array）/ AI 生成 / ADB 截图               │
│  模板匹配 / PaddleOCR / B2 复核引擎（RapidOCR）/ ADB raw 帧截图提速            │
│  / 公告 API 与 HTML 回退                                                    │
├──────────────────────────────────────────────────────────────────────────────┤
│  数据层 (src/data/)                                                           │
│  Pydantic 模型 + DataFacade（facade / manager / issues 解环拆分）+ JSON 持久化  │
│  ComboManager 落盘按 (-rating, hero1_id, hero2_id) 稳定排序                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

上图为**目录层视图**，描述的是代码物理位置。知识库（RAG）是一个跨目录的纵向功能：`src/rag/` 与 `src/scripts/` 是独立的 RAG 专用目录，`src/business/rag/` 与 `src/ui/maintenance/` 则物理嵌套在 business 与 ui 之下。因此知识库在文档中按**单一模块**整体叙述（见 `./module_rag.md`），而不是拆散到各层里。阅读上图时，标注 RAG 的两层行只是说明代码位置，职责细节请转到知识库模块文档。

## 子模块文档索引

| # | 模块 | 目录 | 主要职责 |
|---|------|------|---------|
| 1 | [应用入口与配置](./module_config.md) | `src/main.py` + `src/config/` | 应用启动、环境配置、日志初始化 |
| 2 | [数据模型与数据管理](./module_data.md) | `src/data/` | Pydantic 模型定义、CRUD 操作、JSON 持久化（含 RAG 源数据仓储、ComboManager 稳定排序落盘与**逻辑删除**、**武将变更时间轴 `hero_timeline`**、**卡牌百科快照 `card_sync_store`**、**百科差异忽略名单 `baike_ignore_store`**，DataFacade/manager/issues 解环拆分） |
| 3 | [爬虫与数据采集](./module_scraper.md) | `src/scraper/official_source/` | 官网 JS chunk 字符级状态机解析、数据清洗、头像下载、公告采集与百科 diff、**卡牌百科抓取清洗与逐卡 diff 基元 `card_baike`** |
| 4 | [AI 批量生成](./module_ai_batch.md) | `src/scraper/ai/` | AI 攻略/相性生成、JSON 提取、双模式生成器 |
| 5 | [业务服务层](./module_business.md) | `src/business/` | QProcess 子进程管理、服务编排、官方榜单图片导入、公告更新检查、**卡牌百科同步 `CardSyncService`**、**选将/巅峰赛/对局攻略三板块共享一次截图的轮询串联**、巅峰赛识别编排与禁选建议、**巅峰赛选将人工确认残留修复（导入前校验 + 停止清空 + 牌面在位守卫）** |
| 6 | [知识库（RAG）与元规则维护](./module_rag.md) | `src/rag/` + `src/business/rag/` + `src/ui/maintenance/` + `src/scripts/`（语料与维护脚本） | 语料构建与 ODS/DWD/mart 分层（10 任务 / 12 语料文件 / 2128 块）、向量索引与混合检索、RAG 注入 AI 生成、**武将变更时间轴驱动的语料块版本戳与默认只召当前版本**、索引精化三层架构、元规则 T0 文档维护、数据源编辑与审计 |
| 7 | [屏幕采集与 OCR](./module_capture_ocr.md) | `src/capture/` + `src/ocr/` | ADB 截图与 MuMu 实例探测（`probe_all_devices*` / `probe_running_devices`）、模板匹配、PaddleOCR 识别、官方榜单版式解析、424 字静态汉字特征缓存 |
| 8 | [UI 界面层](./module_ui.md) | `src/ui/` | 主窗口（AppServices 组合根 + StatusChips + ProgressReporter / PollCoordinator / AnnouncementUpdateCoordinator 协调器）、对话框体系、推荐面板、武将浏览器、巅峰赛选将面板（**人工确认残留修复：同名槽位告警 + 会话结束清待确认**）、`MasterDetailPane` 主从列表骨架与实战配队三入口 |
| 9 | [巅峰赛与实战配队](./module_peak_combos.md) | `src/ui/match/peak_*` + `src/business/analysis/peak_ban_advice.py` + `src/business/recognition/peak_select_watcher.py` + `src/data/combo_*` + `src/ocr/card_grid_detector.py` | 巅峰赛（2v2）选将实时识别循环（**会话制互斥 + 会话世代校验 + 候选面板持续刷新治理 + 人工确认残留修复**）、禁选建议象限判定、实战配队（combos）数据管理、座次解析与配队导入 |

## 本轮文档校准（2026-09-21）

自基线 `624c8c5`（2026-09-15）以来，本轮校准覆盖了以下变更：

**新增能力**
- **B2 复核模式**（d88fc2f）：`paddle_loader.py` 新增 `create_rapidocr_ocr()` 构造 PP-OCRv6-small/ONNX 复核引擎（RapidOCR），`get_recheck_ocr_engine()` 惰性加载+失败熔断；`RapidOcrEngine` 包装层将 RapidOCR 结果翻译为 paddleocr 2.x 风格；设备固定 CPU，det 参数 `limit_type=max/limit_side_len=960` 与生产画布同口径；模型缺失直接报错熔断绝不联网下载；frozen 下复制到 %TEMP% 纯 ASCII 路径；新增配置参数 `MUMU_OCR_RECHECK_ENABLED`
- **白名单治理与错法半自动补对闭环**（9ca1b91）：`pending_stats.py`（OCR 未决错法频次与人工确认答案记录，双事件流 + 60 秒节流窗口 + 原子替换写入）、`whitelist_config_dialog.py`（错法观察清单 A+/A/B/C 分类排序 + 用户层白名单维护 `ocr_confusion_overrides.json` + 静态冲突检查 + `reset_ocr_recognizer_cache()` 即时生效）；`character_similarity.py` 新增 `find_whitelist_conflicts()` 静态冲突检查；拼图画布按检测器工作尺度(960)分块修复
- **轮询闲置自动暂停**（bca4092）：`frame_fingerprint.py`（整帧降采样指纹 32×18 灰度 576 字节，MAD 阈值 3.0）、`poll_coordinator.py`（QObject 轮询协调器，强类型 `PollOutcome`/`PollTaskResult`/`PollResult`，连续 5 分钟无画面变化自动暂停，三路交互恢复）、`calibrate_idle_threshold.py`（闲置阈值校准工具）；`ocr_service.py` 新增 `pause_for_idle()`/`is_poll_idle_paused()` 等方法；新增配置参数 `MUMU_OCR_POLL_IDLE_PAUSE`
- **合规化改造**（637102b）：`disclaimer_state.py`（免责声明状态管理，版本感知仅条款更新时重新确认）、`disclaimer_dialog.py`（免责声明对话框）；启动时检查免责声明展示后进入；`emulator_operation_service.py` / `adb_screen.py` 模块文档字符串增加架构防火墙声明（明确不包含 ADB 输入能力）；爬虫每次抓取前存档 robots.txt 到 `logs/robots_cache/`；LICENSE / TERMS.md 新增附加使用条款与法律红线
- **模板匹配分层加速与 ADB raw 帧截图提速**（cd35c98）：`ocr_baseline.py`（OCR 回归基线工具，对比标签文件验证识别一致性）；`capture_for_poll()` 直接返回 numpy 数组跳过 PIL 解码；`screencap_raw()` 新增 raw 帧截图方法；`template_manager.py` 模板匹配分层加速（先粗筛后精匹配）；新增配置参数 `MUMU_SCREENSHOT_MODE`
- **巅峰赛优化**（e39b746）：兜底人工确认不再随拍重置；逐拍内容验证加宽限淘汰，确认优先于自动结论；单拍闭包缺名进宽限不丢确认，读数原文指纹兜底稳定错读的牌，连续多拍验证不到才淘汰
- **实战配队增强**（4fa9a5d）：导入合并保护——手工记录 `manual=True` 同 key 冲突优先保留，逻辑删除记录 `deleted=True` 无条件保留并屏蔽同 key 源记录；座次解析增强 + position 交叉校验；合并 2026-09 外部配队数据
- **对局攻略修复**（ec81790）：名字未决时仍按座次划分敌我，候选内纠错不再清空重划；`LineupState` 按座次划分敌我的逻辑修复
- **OCR 词表外新武将共识保护**（2c49eba）：新增 `unknown_new_hero` 判定——当多个候选都不匹配但词表外新武将名高度相似时输出信号供人工确认
- **OCR 4字武将名拆框修复**（09c904d）：回退证据改用 gamma 差异视图（替代增强）；根除选将页 4 字武将名拆框误判；批量预处理去增强
- **知识库归类/专属牌名单同步**（241e965）：爬虫更新 heroes.json 后归类/专属牌名单随刷新入口同步加载
- **字形缓存补齐**（a1f5ff0 / 2583571）：补齐「存祖逖」等 29 字基线字形缓存
- **新增武将**（0bd1228）：新增王导、祖逖、魏华存三名武将；同步修正魏咎、公孙瓒存量数据

**修正的失效描述**
- 代码规模台账：110+ 文件 / 1280+ 个 `test_*` 函数（原 106 文件 / 1184 个）
- 新增测试文件 4 个（`test_ocr_recheck.py` / `test_whitelist_config.py` / `test_poll_idle_pause.py` / `test_disclaimer_dialog.py` / `test_disclaimer_state.py` / `test_retry_classification.py`）

**已知但未处理的死代码**（按"发现只提醒、不擅自删除"原则保留）
- `src/business/maintenance/classification_suggest.py` 与 `classification_suggest_worker.py`：武将 LLM 建议归类功能，当前 `src/` 内零调用
- `src/config/env.py::list_api_profiles()`：当前仅测试消费

> （2026-09-29 校准更新：上一条已失效——`classification_suggest_worker.py` 已随死代码清理删除，`classification_suggest.py` 经 `HeroClassificationPanel`（知识库维护·武将归类）接入使用；`list_api_profiles()` 现状不变。详见下方 2026-09-29 校准记录。）

## 本轮文档校准（2026-09-29）

自基线 `0007fc4`（含工作树未提交改动）以来，本轮校准覆盖了以下变更：

**架构与主窗口**
- **架构分层收口**（363188a / 9454849）：UI 数据 import 白名单落地（UI 层构造数据层禁令，`tests/test_architecture.py` 以 `ALLOWED_DATA_CONSTANTS` / `ALLOWED_DATA_FUNCTIONS` 白名单守护）、榜数据 provider 注入（对局攻略/巅峰赛胜率与推荐指数改经 provider 回调获取）、`FILE_LINE_BUDGETS` 行数棘轮守护防止单文件行数反弹
- **主窗口拆分**（1f9417b）：`MainWindow` 上帝类四阶段拆分——协作对象装配收敛到 `AppServices` 组合根，状态栏常驻态由 `StatusChips` 自足组件承载，进度出口统一走 `ProgressReporter`，OCR 轮询与公告更新两条管线分别下沉 `PollCoordinator` / `AnnouncementUpdateCoordinator`
- **src/data 解除循环依赖**（2882833）：`DataFacade` 拆分至 `facade.py`、`DataIssue` / `LoadReport` 值对象拆分至 `issues.py`，`manager.py` 回归管理器基类与默认路径（审计 F3）
- **官方榜单导入服务按职责域拆分**（55e3587）：纠错规则、引擎策略与数据联动各归其位

**功能与修复**
- **百科差异条目级忽略名单**（0007fc4）：新增 `src/data/baike_ignore_store.py`（`data/baike_ignore.json`，state+hash 定位"同一差异"，武将/卡牌两段覆盖式保存）与 `src/ui/data_admin/baike_ignore_manager_dialog.py` 管理界面（主窗口「数据」菜单全局兜底入口）；服务层 diff 产出即过滤，公告 ready 判定一致压制；另含状态栏消息超时回落、对局攻略胜率榜按对局链路区分（`WIN_RATE_MODE_2V2` / `WIN_RATE_MODE_PEAK`）
- **菜单栏重排**（工作树未提交）：菜单栏重排为 文件（重新加载数据 / 退出）/ 配置（API 配置、模拟器配置、势力配色、白名单配置）/ 数据（公告与百科 4 项 ▸ 武将获取、攻略生成、武将相性三个生成子菜单 ▸ 官方数据导入、实战配队导入、清空攻略/相性数据）/ 帮助（关于）；原「导入」菜单取消，「攻略获取」改名「攻略生成」，「数据管理」改名「清空攻略/相性数据」并入「数据」菜单
- **多批次全仓零引用死符号清理**（869dcac / b7d6ef8 / fac671f / c85d60a / e9436c2）：2026-09-21 记录的 `classification_suggest_worker.py` 已随之删除，`classification_suggest.py` 经 `HeroClassificationPanel`（知识库维护·武将归类）接入使用、不再是死代码；`list_api_profiles()` 现状不变（仅测试消费）
- **其他**（7116489 / d43d549 / f77559c）：巅峰识别页名独立化与派生 ROI 分辨率自适应缩放；RAG 快照读写下沉 `scripts/snapshot_common` 解除 sync/audit 互引环；audit 数据段比对恢复独立降级

**修正的失效描述**
- 代码规模台账：112 文件 / 1337 个 `test_*` 函数（原 110+ / 1280+）
- 字形特征缓存 424 字（原 365 字）；RAG 语料块 2128 块（原 2090 块）
- 官方数据导入实为 2v2 / 巅峰赛 / 武将放逐三类榜单（原描述缺巅峰赛）；AI 生成后端为多供应商档案（deepseek / openai / ollama / openai-compatible），非仅 DeepSeek
- 配置默认值与键：`mumu_ocr_recheck_enabled` 默认 `False`；`MUMU_OCR_POLL_IDLE_MINUTES` 配置键已移除，闲置阈值固定为 `PollCoordinator.IDLE_PAUSE_MINUTES = 5`；`check_disclaimer()` 已拆分为 `should_show()` / `accept()`（详见 `./module_config.md`）

## 本轮文档校准（2026-10-01）

自基线 `885ea96`（含工作树未提交改动）以来，本轮校准覆盖了以下变更：

**功能与数据同步**
- **官网武将数据同步**（74234a8）：新增谢灵运、陶渊明两名武将（`heroes.json` 186 名）；`mjs_adjustments.json` 登记左思技能调整（2026-09-30 事件追加，事件总数 141 → 144，`source=announcement` 15 → 18 条）；`model_pricing.json`（`config/model_pricing.json`）新增 `sensenova-6.8-flash-lite` 计价条目；`char_info_cache.json` 静态汉字特征缓存基线 396 → 424 字
- **巅峰赛选将人工确认残留修复**（48b0f99）：`peak_select_watcher.py` 新增图片导入前旧确认校验（`verified_resolutions_for_import()`，与实时循环共用定位语义但一次性快照不宽限、无法定位的确认立即丢弃）、停止识别后清空确认表/读数指纹/失验计数/禁将基线、`confirm_pending()` 牌面在位守卫（无在识别中的牌面时拒绝确认并提示"牌面已不在识别中，确认未生效"）；`peak_select_panel.py` 同名槽位显性告警（候选池出现重复武将名时写日志 + 追加识别日志 + 状态栏 warning 提示复核，不再静默去重隐藏"14 张牌只显示 13 个名字"），会话结束（停止/牌面退出）摘除待确认行防误点

**架构与死代码清理**
- **Phase 4 上帝类拆分**（885ea96）：六大目标按职责域出仓 14 个新模块，棘轮与文档同步收缩（详见 `./module_business.md` / `./module_ui.md` / `./module_rag.md` / `./module_capture_ocr.md`）
- **API 档案域拆分**（78fd65c）：`profiles.py` 自 `env.py` 拆出（审计 G7，`env.py` 641 → 363 行），同步清理 rag / recognition / data / ocr 四域生产死代码
- **apply_incremental_update 迁移**（ee332c4）：自 `manager.py` 迁至 `facade.py` 消解类型级循环，架构守护扩展类型边与字段/行数棘轮
- **多批次死代码清理**（bf7a0df / fd1ed15 / 6c541b3 / 4455094 / 07be64d）：删除武将选择对话框无效 `ReturnFormat` 参数管线（D1）、模板匹配零消费 `last_match_confidence` 及死存储（D2）、`VALID_KINDS` / `last_match_time` / `_updated_at` / `_selected_*` 四处写后不读字段（D3–D6）、UI 三处写后不读属性（D7/D8）、全项目无消费点的 `RAG_PROJECT_DIR` 预留键（D9）

**修正的失效描述**
- 代码规模台账：112 文件 / 1350 个 `test_*` 函数（原 112 / 1337，巅峰赛修复补测 13 例）
- 字形特征缓存 424 字（原 396 字）；RAG 语料块 2128 块（原 2118 块，`武将RAG语料` 639 → 647、`武将分类语料` 184 → 186）
- 武将数 186 名（原 180 名）；`hero_classification.json` 实测 7 个顶层键（version / updated_at / source / note / categories 16 项 / hero_categories 186 键 / counter_chain 8 键）；`mjs_adjustments.json` 实测 4 个顶层键（init_imported_at / init_source_last_updated / corpus_base_date / events 144 条）
- `model_pricing.json` 实际位于 `config/model_pricing.json`（非 `data/`），已随 `sensenova-6.8-flash-lite` 新增计价
- `main_window.py` 行数预算实际为 552 行（审计 G1 拆分后棘轮值，`module_ui.md` 第七章原误记 819 已修正）；新增 `src/data/corpus_fields.py`（语料索引字段契约单一来源，12 行）入表
