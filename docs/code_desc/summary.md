# 名将杀 Agent — 项目总览

> 文档日期：2026-10-25（基线 `ed8b4ac`）
> 项目路径：`G:\py_savepoint\test_project`  
> 远程仓库：`gitee.com:chen-xianghao920/test_project.git`

## 项目简介

名将杀 Agent 是一款面向[名将杀手游](https://mjs.ztgame.com/)的桌面辅助工具。它提供武将数据库查询、AI 批量攻略/相性生成、武将相性分析、实时屏幕采集与 OCR 武将识别、RAG 语料知识库维护等功能，帮助玩家在游戏中快速决策。RAG 语料维护含索引精化工作台，将 LLM 建议编排、清单状态管理与持久化写回下沉为纯业务层，与 UI 解耦。

自基线 `624c8c5`（2026-09-15）以来，项目新增 B2 复核模式（PP-OCRv6-small/ONNX 未决槽位候选确认）、白名单治理（错法频次记录 + 人工确认 + 用户层白名单维护）、轮询闲置自动暂停（整帧指纹判闲 + 三路恢复）与合规化改造（免责声明弹窗 + 附加法律条款 + robots.txt 存档）。2026-09-29 后进一步落地 Phase 4 上帝类拆分（六大目标按职责域出仓 14 个新模块）、API 档案域拆分、巅峰赛选将人工确认残留修复（导入前校验、停止清空确认表/禁将基线、牌面在位守卫、同名槽位显性告警）与官网武将数据同步（新增谢灵运/陶渊明）。2026-10-02 完成审计 G8：CaptureService 职责域出仓（官方导入网关 `official_import_gateway` / 图像保存调度 `image_save_scheduler` / OCR 任务协调器 `ocr_task_coordinator` 三模块，670→570 行，UI 门面信号零改动）、`save_results` 落盘 page_type 参数化（不再硬编码 `wujiang_select`）与行数棘轮 500 行 tripwire 落地（12 个 >500 行文件补种入册，超线未登记即测试变红）。2026-10-03 起实施数据出库与运维工具链建设：抓取与维护数据移出版本库、改经私有数据仓同步（`pull_data.py`，`data/SOURCES.md` 登记来源），落地 doctor 一键自检 / ops 周更编排 / 任务台账 / 版本单一来源 / 写前快照与全库原子写收敛 / 采集写入守卫。2026-10-06 完成 **B1 OCR 全量切换**——RapidOCR 双套件（PP-OCRv6-small 主引擎 + PP-OCRv4-mobile 复核引擎，ONNX/CPU）完全替代 paddle 全栈，打包含模型精简包 763MB→608MB。2026-10-07 至 10-11 完成三件大事：**竞态守卫原语定稿**（`session_guard.py` 三原语 SessionGuard/BusyGate/OneShotToken，全仓五处裸旗标收口）、**RAG 编码器 ONNX 化**（检索/建索引默认走 onnxruntime，`embedder.py` 编码器抽象层 + `export_embedding_onnx.py` 导出链 + 检索 trace 可观测 + 索引整目录重建，精简包自带完整 RAG，两包 658.9/764.1MB）、**数据新鲜度芯片**（`freshness.py` 逐武将核对判定核心 + `data_freshness_chip.py` 状态栏第三枚胶囊，修正全局误绿漏报）；并完成打包态读写根分裂治理（榜单/抓取/配置/头像/规则摘要读取侧统一运行时可写根）与 CaptureService 连接域出仓（`CaptureConnectionManager`）。

2026-10-12 至 10-23 完成四组改造：**首启向导三段体检**（`onboarding_dialog.py` 一页式环境自检→模拟器连接→试识别 + `onboarding_state.py` 版本感知状态 + `onboarding_fixes.py` 修复动作库，帮助菜单可重看，`MJS_ONBOARDING_FORCE=1` smoke 强制通道）、**爬虫合规加固与访问披露**（如实爬虫 UA `mjs-agent/1.0` 取代浏览器伪装 + robots.txt 24 小时缓存与逐 URL `Disallow` 校验 + 生成任务 OS 级互斥锁 `generation_lock.py` + 官网清洗守卫 `hero_fields.py`，TERMS.md 升至 1.3 增补自动化访问披露，免责声明版本 1.1→1.3）、**抓取链路健壮性**（`ScriptRunner` 启动失败终态契约补发 `finished` + 看门狗树杀与 2 秒复查 + 取消链路 Starting 兜底 + 主窗口关闭杀抓取进程树，封堵 VUL-B2-001/002）、**数据安全与备份恢复**（三条数据全灭路径封堵 + `backup_restore_service.py` / `backup_restore_dialog.py` 消费端补齐，恢复前自快照实现双向可回退 + 导入前备份与源结构校验）；另落地相性指定获取双入口拆分（「指定配对计算」+「落后相性刷新」）、巅峰赛面板防跳变（卡位漂移扣帧复核）、分析域重构（`win_rate_ranking.py` / stale 误清修复）、公告与卡牌访问冷却限频（`check_cooldown.py`）、新鲜度损坏留底与阈值可配置（`DATA_FRESHNESS_STALE_GREEN_DAYS` / `_RED_DAYS`）、公告管理器启动即加载，以及 RAG 域审计容错与语料写前快照。

2026-10-24 至 10-25 完成连接域两大改造与导入提速：**多模拟器五品牌连接**（原单文件探测模块拆包为 `emulator_discovery/` 函数式注册表——MuMu/雷电/夜神/逍遥/蓝叠五品牌探测器 + 端口段兜底扫描 + 进程反查，`AdbCapture` 品牌参数化，配置键 `MUMU_*` → `EMULATOR_*` 平滑升级，探测提速五品牌全部 <1s）、**官方 PC 客户端直连**（`pc_client.py` 窗口枚举 + Windows.Graphics.Capture 主路径 + GDI BitBlt 兜底 + 黑帧双指标检测引导，连接方式/设备页双形态，全程只读窗口画面）、**官方榜单导入提速**（20261022 拼接长图切分归一化复用分页链路 + 单格识别 2× 快通道整板提速约 4.6 倍 + 待复核行截图仅保留武将名称格）；另有控制台日志队列化防卡死（Windows 快速编辑选中不再冻结 UI）、公告检查与获取武将变更冷却窗口分立、爬虫 robots/头像下载超时解耦。

## 核心功能

- **资料库浏览** — 在”武将资料”中查询武将详情、技能和攻略；在”卡牌图鉴”中只读浏览官方卡牌及维护独立的效果配置
- **选将推荐** — 4×2 网格展示推荐武将，集成相性评分、胜率排名与 OCR 截图导入
- **对局攻略** — 2×2 展示四名武将，支持 ADB/本地图片导入并加载胜率榜（按对局链路区分 2v2 / 巅峰赛，`WIN_RATE_MODE_2V2` / `WIN_RATE_MODE_PEAK`）
- **AI 攻略生成** — 通过多供应商 API（DeepSeek / OpenAI / Ollama / OpenAI 兼容档案）或浏览器自动化批量生成武将攻略，默认 RAG 官方规则语料增强，可切换经典模式（无 RAG 注入）；API 模式支持扩展额度与思考重试
- **AI 相性评分** — 全量/指定武将的相性评分；RAG 注入双方武将语料块与规则/FAQ/卡牌跨类块，支持 RAG 增强/经典双版本。指定获取自 2026-10-20 起拆为两个入口：「指定配对计算」（`SynergyPairDialog`，按状态筛选武将后按策略组装精确配对清单，支持 2~8 武将两两配对）与「落后相性刷新」（`SynergyRefreshDialog`，列出已有但落后于武将资料的配对、按最后生成日期升序，勾选后只刷这批）
- **屏幕采集与 OCR** — 双形态图像源：ADB 连接五品牌模拟器截图，或官方 PC 客户端窗口直连（WGC 主路径 + BitBlt 兜底，全程只读不发送输入）；OpenCV 模板匹配 + RapidOCR/ONNX 双套件（PP-OCRv6-small 主引擎 + PP-OCRv4-mobile 复核引擎）识别武将名；v4 三件套经 `fetch_recheck_models.py` 官方清单预取（URL+SHA256 钉死），运行时离线纪律绝不联网下载
- **实时轮询** — 统一截图后独立检测武将选择页和对局攻略页，分别维护任务激活状态与冷却时间
- **官方数据导入** — 可独立或同时导入 2v2/巅峰赛/武将放逐榜单图片；按表格行写入三份 CSV，显示 OCR 进度，并以词表候选、逐字补识别和待复核保证名称可靠性
- **公告更新监控** — 手动检查官方公告，仅对 `【新增武将】/【武将调整】` 章节相关公告提醒；百科逐武将 diff 确认后才提示”可更新”，并提供”指定获取+增量”一键精准更新；差异条目可条目级加入忽略名单（`baike_ignore.json`，state+hash 定位同一差异，恢复忽略后自动重现），主窗口提供「百科忽略名单管理」入口；更新流程引入阶段令牌与本地回查防止跨阶段误消费
- **卡牌百科变更捕获** — 手动检查官网手牌库与本地卡牌差异，勾选后应用官网文本覆盖本地（`card_amount` 保留）；快照（`card_snapshot.json`）与变更记录（`card_changes.json`）持久化，驱动卡牌精化时效检查
- **知识库维护（RAG）** — 本地 RAG 语料维护工作台，布局为重排的「左栏维护对象导航 + 右侧数据源工作区 + 底部折叠执行日志」：左栏 10 项（5 个可编辑数据源 专属牌/卡牌点数/装备属性/武将分类/元规则母本 + 5 个只读语料，状态点对齐其语料任务状态）；支持单项 `--only` / 全部 / 加索引三级重建、保存后左栏状态点即时变「待重建」并可就地重建、结构化审计提示并支持跳转定位、10 个语料任务状态常驻可见；**「检索诊断」只读抽屉**（9 列候选去向表：得分/RRF/当前版/as_of/剔除原因，top_k 可调，显示编码后端与耗时，五种丢弃原因中文映射）；源数据已从 xlsx 迁移为 JSON（`data/special_cards.json` / `data/card_points.json` / `data/equip_attrs.json`，xlsx 归档 `data/archive/`）
- **元规则 T0 文档维护** — 以 `docs/元规则整理-完整版.md` 为规则知识库母本（只增不删、机器校验），提供文档校验（audit）、数据段差异同步（sync）、变更提案起草/合入（propose/apply）与疑难登记（pending）完整工作流，并在「知识库维护 → 元规则母本」维护对象（内嵌四个子页签）可视化操作
- **索引精化** — RAG 语料索引字段（timing / trigger_condition / keywords / related）精化工作台，架构下沉为三层纯业务代码：`refinement_service.py`（纯函数：清单扫描/LLM 建议生成/磁盘读写）+ `RefinementSession`（纯 Python 状态层：pending/curated/normal 三池清单、磁盘基线与 LLM 基线双基线、行状态 pending/suggested/modified/refined/generated）+ `SuggestController`（QObject，LLM 线程编排，SuggestWorker 批量建议，含 LIVE_WORKERS 全局僵尸防护列表防止 dialog 销毁后 QThread 运行中析构）
- **B2 复核模式** — 对未决识别槽位，使用 PP-OCRv6-small/ONNX 引擎（RapidOCR）做候选内确认；惰性加载+失败熔断，模型缺失不联网下载，设备固定 CPU；`RapidOcrEngine` 包装层将 RapidOCR 结果翻译为 paddleocr 2.x 风格
- **白名单治理** — OCR 未决错法频次记录（`pending_stats.py`，双事件流 + 60 秒节流窗口 + 原子替换写入）+ 用户层白名单维护界面（`whitelist_config_dialog.py`，错法观察清单 A+/A/B/C 分类 + `ocr_confusion_overrides.json` 静态冲突检查 + `reset_ocr_recognizer_cache()` 即时生效）
- **轮询闲置自动暂停** — 整帧降采样指纹（`frame_fingerprint.py`，32×18 灰度 576 字节，MAD 阈值 3.0）+ 轮询协调器（`poll_coordinator.py`，连续 5 分钟无画面变化自动暂停，三路交互恢复）+ 阈值校准工具（`calibrate_idle_threshold.py`）
- **合规化改造** — 免责声明弹窗（`disclaimer_dialog.py` + `disclaimer_state.py`，版本感知仅条款更新时重新确认，当前版本 **1.3**，对应 TERMS.md 1.3）+ 附加法律条款（LICENSE 第 8 条授权终止）+ robots.txt 存档（`logs/robots_cache/`，24 小时内不重复请求）**并逐 URL 按已解析规则执行**（`Disallow` 路径拒绝抓取，公告接口与图片域名按各自 robots.txt 校验，获取失败放行并告警）+ 如实爬虫标识 `mjs-agent/1.0`（不再伪装浏览器 UA）+ 自动化访问方式披露（TERMS.md 第 7 节：Playwright 附加 `--disable-blink-features=AutomationControlled`、相邻两次生成随机休息 60~180 秒）+ 架构防火墙声明（代码不含 ADB 输入能力）+ **数据出库**（抓取与维护数据自 2026-10-03 起不入版本库，经私有数据仓同步，`data/SOURCES.md` 登记来源）
- **数据新鲜度芯片** — 状态栏第三枚常驻胶囊（`data_freshness_chip.py`）+ 纯业务判定核心（`src/business/freshness.py`，无 Qt 依赖）：按档位判定武将/攻略/相性/榜单四项数据年龄，**档位自 2026-10 起可配**（`DATA_FRESHNESS_STALE_GREEN_DAYS` 默认 7 / `DATA_FRESHNESS_STALE_RED_DAYS` 默认 14；官方榜单约每半月更新，7/14 写死会让榜单长期停在黄/红并盖掉武将信号，TD-A6-001）；武将项**逐事件逐武将核对**（事件落后 ⇔ 该武将在 heroes.json 缺失或 `last_updated` 早于事件日期，批次按公告 ref 去重），修正旧全局 max 对比下"指定获取洗绿"与"混合批次只推增量"两类误绿漏报；时间轴不健康（含损坏留底判定）时退化为纯年龄兜底并出警示行；点击弹「内容年龄 / 数据源周期 / 建议操作」三组菜单，建议动作（增量获取 / 指定获取）可点击直达采集入口（忙碌时 toast 拦截）；刷新自足（六路服务信号 + QFileSystemWatcher 监听 data/ 目录 mtime 快照 500ms 去抖，无轮询定时器）；精简版只判武将+榜单两项
- **首启向导三段体检** — `src/ui/app/onboarding_dialog.py`（551 行，一页式三段：环境自检 → 模拟器连接与分辨率体检 → 试识别）+ `src/config/onboarding_state.py`（`config/.onboarding_state.json`，`ONBOARDING_VERSION="1"` 版本感知，状态缺失/损坏/版本不符一律重弹，跳过不再打扰）+ `src/ui/app/onboarding_fixes.py`（失败修复动作库，`OnboardingFix`/`OnboardingFixResult`）。合规边界写死在模块 docstring：只做只读检查文件与依赖、通过 ADB 读屏、调用 OCR 识别，界面明示"本工具只读屏幕，不操作游戏"。入口：`main.py` 在 `show()` 后调用 `should_show_state()` 弹向导（预热已完成，避免 90~120 秒预热窗口内遮挡）；`MJS_ONBOARDING_FORCE=1` 强制在预热前弹出（发版 smoke 用）；「帮助 → 重看首启引导」可随时重开。设计规格见 `docs/spec/spec_onboarding.md`
- **备份与恢复** — `src/business/maintenance/backup_restore_service.py`（247 行，列出/预览/恢复 `data/backups/` 自动快照的纯业务层）+ `src/ui/data_admin/backup_restore_dialog.py`（410 行，「当前文件 + 最近快照」对照预览键级差异）。安全语义：恢复前先 `snapshot_to_backups()` 快照当前状态（恢复到了更坏状态也能再恢复回来，双向可回退）；恢复用原子写替换目标文件，失败时原文件不变、不产生半截文件；坏备份（结构损坏或含校验失败记录）在预览与恢复两侧都拒绝，禁止把坏备份覆盖到好数据上；武将/相性/攻略存在跨文件引用，单文件恢复会造成引用失配，确认时默认提供整组恢复到同一时间点。入口：「数据 → 从备份恢复」。备份**产生方**已覆盖全部数据写入路径（编辑事务、配队导入覆盖、坏文件留底、AI 生成与官网采集覆盖前快照），本模块只补**消费端**，不改动任何产生方
- **运维自检与周更编排** — `doctor.py` 一键只读自检（12 组检查：环境/设备/私有仓/数据/**RAG 索引**/配置/白名单/AI 链路/日志/磁盘/合规/安全，`--offline` / `--json`；`run_checks(offline, checks)` 支持按组选择子集，`check_environment` 已拆为 `check_dev_toolchain` 与 `check_runtime_env` 以适配打包态）；`ops.py` 周更运维统一入口（weekly 编排 pull→diff→maintain_rag→pytest→push，前置失败不 push；health / retry-failed / clean 子命令）；任务台账 `task_ledger.py`（`logs/task_results.jsonl`，跨日志轮转窗口留痕）；AI 定向重试 `ai_batch --retry-failed`（失败清单 `logs/ai_last_failures.json`）；版本单一来源 `src/config/version.py`（`app_version()`，VERSION 文件 1.2.1）；**RAG 评估历史 `logs/rag/eval_history.jsonl`**（eval_rule_faqs / eval_guide_quality 每次运行默认追加编码后端与模型指纹，`--no-record` 逃生）

## 技术栈

| 层次 | 技术 |
|------|------|
| 桌面 UI | PySide6（Qt for Python） |
| 数据模型 | Pydantic v2（数据校验与序列化） |
| AI 生成 | httpx（多供应商 OpenAI 风格 API 同步请求）/ Playwright（浏览器自动化） |
| RAG 检索 | ChromaDB + 编码器抽象层 `embedder.py`（默认 onnxruntime fp32 CLS 池化，`tokenizers` 直读 tokenizer.json；`RAG_EMBEDDING_BACKEND=st` 回滚 sentence-transformers；bge-small-zh-v1.5，三件套 sha256 侧车校验）+ 关键词 RRF 混合检索 |
| 屏幕采集 | ADB（Android Debug Bridge）exec-out 截图（五品牌模拟器）+ `windows-capture==2.0.1`（官方 PC 客户端 WGC 主路径，Win10 1903+）+ ctypes GDI BitBlt 兜底 |
| 图像处理 | OpenCV（模板匹配、表格横线检测）、Pillow（图像格式转换） |
| OCR 识别 | RapidOCR 3.9.2 / ONNX 双套件（PP-OCRv6-small 主引擎 + PP-OCRv4-mobile 复核引擎，`onnxruntime` 1.23.2，CPU 固定、线程可钉）+ 编辑距离矫正 + 汉字特征评分（`unihan_etl` / `cnradical` / `pypinyin`，437 字特征缓存）；paddle 全栈已退役 |
| 数据持久化 | JSON + CSV 文件（`json_repository` 原子写/写前快照基元，无数据库依赖） |
| 测试与静态检查 | pytest 9.0.3（156 文件 / 2042 个 `test_*` 函数，CI `-n auto --timeout=60`）+ Ruff 0.12.0（`F` / `T201` / `I` / `B905`）+ vulture / pyright（CI report-only） |
| 异步通信 | QProcess（子进程管理）+ Qt Signal/Slot |

## 整体目录结构

```
test_project/
├── src/
│   ├── main.py                  # 应用入口（免责声明 + 启动画面 + OCR 阻塞预热 + 首启向导）
│   ├── config/                  # 配置管理（.env 解析、日志配置、disclaimer_state 免责声明状态 1.3、onboarding_state 首启向导状态）
│   ├── data/                    # 数据模型与数据管理层（DataFacade 门面 + manager/issues 解环拆分；含 hero_timeline 武将变更时间轴、card_sync_store 卡牌百科快照、baike_ignore_store 百科忽略名单）
│   ├── scraper/                 # 爬虫与 AI 批量生成层（含 card_baike 卡牌百科抓取清洗、hero_fields 官网清洗守卫）
│   │                           #   + ai/generation_lock（AI 批量生成 OS 级互斥锁）
│   ├── rag/                     # RAG 向量索引与混合检索基础设施（ChromaDB + embedder.py 双后端编码器抽象（onnx 默认 / st 回滚）+ 关键词 RRF）
│   ├── scripts/                 # 语料构建与维护脚本（build_*_corpus / maintain_rag / import_hero_adjustments / 元规则维护 CLI）
│   │                           #   + doctor（一键只读自检，含 RAG 索引组）/ ops（周更运维统一入口）/ pull_data（私有数据仓同步）
│   │                           #   + fetch_recheck_models（v4 复核模型预取）/ ocr_baseline（OCR 回归基线）/ calibrate_idle_threshold（闲置阈值校准）
│   │                           #   + export_embedding_onnx（ONNX 编码器导出链，dev）/ rag_eval_history（RAG 评估历史 JSONL）
│   ├── business/                # 业务服务层（QProcess、OCR/官方榜单导入编排、公告与巅峰赛业务、卡牌百科同步）
│   │                           #   + emulator 协作模块（capture_service 门面 + capture_connection 连接域 + ocr_task_coordinator / official_import_gateway / image_save_scheduler，审计 G8+P1-8）
│   │                           #   + recognition/session_guard（SessionGuard / BusyGate / OneShotToken 竞态守卫三原语）
│   │                           #   + freshness.py（数据新鲜度判定核心，逐武将核对）+ pending_stats（OCR 未决错法频次与人工确认记录）
│   │                           #   + maintenance/backup_restore_service（备份快照列出/预览/恢复消费端）
│   │                           #   + access_cooldown/check_cooldown（访问冷却与并发守卫）/ execution/script_runner（子进程看门狗与树杀）；任务台账 task_ledger 为底层横切设施，位于 src/config/
│   │                           #   + emulator/layout_resolution + analysis/win_rate_ranking（排名/量纲收敛）
│   ├── capture/                 # 屏幕采集层（ADB 连接与截图、多品牌模拟器探测 emulator_discovery/、官方 PC 客户端直连 pc_client.py；仅屏幕读取，无输入能力）
│   ├── ocr/                     # OCR 识别层（模板匹配 + RapidOCR 双套件 + 卡位检测 + engine_loader 引擎装载）
│   └── ui/                      # PySide6 用户界面层（app / configuration / data_admin / generation / library / match / maintenance / recommendation / shared）
│                               #   + 主窗口协调器与自足组件（app_services / status_chips / data_freshness_chip 数据新鲜度芯片 / progress_reporter / poll_coordinator / announcement_update_coordinator）
│                               #   + onboarding_dialog（首启向导三段体检）/ onboarding_fixes（失败修复动作库）
│                               #   + data_admin/ignoreable_diff_list（忽略差异列表 mixin）/ whitelist_config_dialog（白名单配置）/ baike_ignore_manager_dialog（百科忽略名单）/ frame_fingerprint（闲置指纹）
│                               #   + data_admin/backup_restore_dialog（备份恢复）/ generation/synergy_refresh_dialog（落后相性刷新）
│                               #   + disclaimer_dialog（免责声明对话框）
├── data/                        # 数据文件（JSON + 2v2/巅峰赛胜率出场、放逐 CSV；RAG 源数据 JSON 与归档 archive/；含 card_snapshot.json / card_changes.json 卡牌百科快照与 baike_ignore.json 忽略名单）
│                               #   源数据自 2026-10-03 起不入版本库，经私有数据仓同步（SOURCES.md 登记来源，samples/ 为虚构示例）
│                               #   + backups/（写前快照自动备份，扁平命名 data__<文件名>-<时间戳>.json，keep=10 轮转）
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
│  QProcess 子进程管理、ADB 截图编排（连接域出仓 CaptureConnectionManager）、     │
│  OCR 轮询控制（含闲置暂停治理）；竞态守卫三原语 session_guard                  │
│  （SessionGuard 世代 / BusyGate 忙碌闸 / OneShotToken 一次性令牌）             │
│  数据新鲜度判定核心 freshness.py（逐武将核对，无 Qt 依赖）                     │
│  RAG 索引精化：refinement_service（纯函数）+ RefinementSession（三池状态）     │
│  + SuggestController（LLM 线程编排，僵尸防护）                                │
│  卡牌百科：CardSyncService（手动检查官网手牌库差异，勾选后应用更新）              │
│  白名单：pending_stats（未决错法频次记录与人工确认答案收集）                    │
│  元规则：rule_doc_service（纯函数）+ audit_service + hero_brief + task_defs  │
│  无 UI 引用，通过 Signal 通信                                                 │
├──────────────────────────────────────────────────────────────────────────────┤
│  采集层 (src/scraper/ + src/capture/ + src/ocr/)                             │
│  官网 JS 字符级状态机解析（extract_js_array）/ AI 生成 / ADB 截图               │
│  模板匹配 / RapidOCR 双套件（v6 主 + v4 复核）/ ADB raw 帧截图提速             │
│  / 公告 API 与 HTML 回退                                                    │
├──────────────────────────────────────────────────────────────────────────────┤
│  数据层 (src/data/)                                                           │
│  Pydantic 模型 + DataFacade（facade / manager / issues 解环拆分）+ JSON 持久化  │
│  ComboManager 落盘按 (-rating, hero1_id, hero2_id) 稳定排序                    │
│  json_repository 原子写/写前快照基元全库收敛；源数据出库经私有数据仓同步        │
└──────────────────────────────────────────────────────────────────────────────┘
```

上图为**目录层视图**，描述的是代码物理位置。知识库（RAG）是一个跨目录的纵向功能：`src/rag/` 与 `src/scripts/` 是独立的 RAG 专用目录，`src/business/rag/` 与 `src/ui/maintenance/` 则物理嵌套在 business 与 ui 之下。因此知识库在文档中按**单一模块**整体叙述（见 `./module_rag.md`），而不是拆散到各层里。阅读上图时，标注 RAG 的两层行只是说明代码位置，职责细节请转到知识库模块文档。

## 子模块文档索引

| # | 模块 | 目录 | 主要职责 |
|---|------|------|---------|
| 1 | [应用入口与配置](./module_config.md) | `src/main.py` + `src/config/` | 应用启动、环境配置、日志初始化（**控制台日志队列化 `_QueuedConsoleHandler`，Windows 快速编辑选中不再冻结 UI**）、**版本单一来源 `version.py`**、**启动自动公告检查与截图回收**、**env 键元表 `_ENV_KEY_SPECS` 单源（38 键，新增 `EMULATOR_TYPE` / `EMULATOR_BRAND` / `PC_WINDOW_TITLE` / `EMULATOR_ADB_*` 多品牌连接键，`default_from` 回退旧 `MUMU_*`）**、**首启部署配色/价格可改副本到运行时根**、**免责声明版本 1.3（TERMS.md 1.3）**、**首启向导状态 `onboarding_state`（版本感知三段体检）** |
| 2 | [数据模型与数据管理](./module_data.md) | `src/data/` | Pydantic 模型定义、CRUD 操作、JSON 持久化（含 RAG 源数据仓储、ComboManager 稳定排序落盘与**逻辑删除**、**武将变更时间轴 `hero_timeline`**、**卡牌百科快照 `card_sync_store`**、**百科差异忽略名单 `baike_ignore_store`**，DataFacade/manager/issues 解环拆分）、**json_repository 四基元全库收敛（原子写 / 写前快照 / `preserve_corrupt_file` 坏文件留底 / `save_payload` 加载完整性守卫）**、**数据出库与私有数据仓同步（pull_data）**、**榜单/推荐指数 CSV 仓储读侧统一运行时可写根**、**推荐指数 stale 判定纯读化（`>=` 且不再写回状态）** |
| 3 | [爬虫与数据采集](./module_scraper.md) | `src/scraper/official_source/` | 官网 JS chunk 字符级状态机解析、数据清洗（**新增 `hero_fields` 官网字段清洗与缺 id 健壮性**）、头像下载、公告采集与百科 diff、**卡牌百科抓取清洗与逐卡 diff 基元 `card_baike`**、**全量四道规模写入守卫 + 增量损坏硬停**、**抓取输出默认路径归运行时可写根**、**如实爬虫 UA `mjs-agent/1.0` + robots.txt 24 小时缓存与逐 URL `Disallow` 执行**、**抓取取消链路与空目标伪成功修复** |
| 4 | [AI 批量生成](./module_ai_batch.md) | `src/scraper/ai/` | AI 攻略/相性生成、JSON 提取、双模式生成器、**httpx 超时分层 + 定向重试 `--retry-failed`**、**prompt 四层决策门/锚例校准/JSON 纪律**、**双生成器后处理共享管线 `normalize_and_validate_*`**、**MAX_OUTPUT_TOKENS 单源 env 键元表**、**生成任务 OS 级互斥锁 `generation_lock`（msvcrt/fcntl + holder 旁路文件）**、**批次中断状态与退出杀进程树**、**浏览器会话超时自动重建** |
| 5 | [业务服务层](./module_business.md) | `src/business/` | QProcess 子进程管理（**`ScriptRunner` 启动失败终态契约 + 看门狗树杀与 2 秒复查 + 完成钩子重入进程保护**）、服务编排、官方榜单图片导入（**20261022 拼接长图切分复用分页链路 + 单格识别 2× 快通道整板提速约 4.6 倍**）、公告更新检查（**公告检查与获取武将变更冷却窗口分立**）、**卡牌百科同步 `CardSyncService`**、**选将/巅峰赛/对局攻略三板块共享一次截图的轮询串联**、巅峰赛识别编排与禁选建议、**巅峰赛选将人工确认残留修复（导入前校验 + 停止清空 + 牌面在位守卫）**、**任务台账 `task_ledger` 与子进程看门狗（30 分钟）**、**OCR worker 首建同步回调接线（消除官方导入锁死竞态）**、**竞态守卫三原语 `session_guard`（SessionGuard/BusyGate/OneShotToken）**、**连接域出仓 `CaptureConnectionManager`（模拟器 ADB / PC 客户端双形态）**、**数据新鲜度判定核心 `freshness.py`（逐武将核对 + 阈值可配 + 损坏留底）**、**备份恢复消费端 `backup_restore_service`**、**访问冷却与并发守卫 `check_cooldown`**、**胜率排名量纲收敛 `win_rate_ranking`** |
| 6 | [知识库（RAG）与元规则维护](./module_rag.md) | `src/rag/` + `src/business/rag/` + `src/ui/maintenance/` + `src/scripts/`（语料与维护脚本） | 语料构建与 ODS/DWD/mart 分层（10 任务 / 12 语料文件 / 2128 块）、向量索引与混合检索、**编码器抽象层 `embedder.py`（onnxruntime 默认 + st 回滚档，三件套 sha256 熔断）**、**检索 trace 可观测（五种丢弃原因 + `--explain`）**、**索引整目录重建（rmtree 清孤立段，80.9→14.8MB）**、**检索诊断抽屉**、RAG 注入 AI 生成、**武将变更时间轴驱动的语料块版本戳与默认只召当前版本**、**语料块数校验快照基线三态（int / snapshot / None）**、索引精化三层架构、元规则 T0 文档维护、数据源编辑与审计、**审计容错与语料写前快照（用量补记）**、**建议线程并发收口（SuggestController 串行化）** |
| 7 | [屏幕采集与 OCR](./module_capture_ocr.md) | `src/capture/` + `src/ocr/` | 双形态屏幕采集（ADB 截图 + **官方 PC 客户端直连 `pc_client.py`**：WGC 主路径 + BitBlt 兜底 + 黑帧检测）、**多品牌模拟器探测（`emulator_discovery/` 注册表：MuMu/雷电/夜神/逍遥/蓝叠 + 端口段兜底 + 进程反查）**、模板匹配、**RapidOCR 双套件识别（v6 主 + v4 复核，`engine_loader` 装载）**、官方榜单版式解析（**含 20261022 拼接长图切分**）、437 字静态汉字特征缓存、**白名单 10 对基线与武将库冲突重检**、**`_recognize_page` 页型模板方法（`_PagePlan`）**、**兜底 OCR 批次零产出短路（det 调用 14→4）**、**识别状态词表单源三常量**、**卡位逐拍漂移抑制与面板跳变治理（`card_grid_detector`）** |
| 8 | [UI 界面层](./module_ui.md) | `src/ui/` | 主窗口（AppServices 组合根 + StatusChips + **DataFreshnessChip 数据新鲜度芯片** + ProgressReporter / PollCoordinator / AnnouncementUpdateCoordinator 协调器）、对话框体系、推荐面板、武将浏览器、巅峰赛选将面板（**人工确认残留修复：同名槽位告警 + 会话结束清待确认**）、`MasterDetailPane` 主从列表骨架与实战配队三入口、**资料库编辑 AI 生成忙碌守卫**、**ComboStrip 信号契约修复（Signal(object)）**、**设备离线计入闲置暂停**、**实战配队批量生成对话框按配队武将筛选**、**忽略差异列表 mixin `IgnoreableDiffListMixin` 收口**、**头像路径两级回退（运行时根 → 随包基线）**、**首启向导 `onboarding_dialog`（三段体检）+ `onboarding_fixes`**、**备份恢复对话框 `backup_restore_dialog`**、**落后相性刷新 `synergy_refresh_dialog`**、**公告管理器启动即加载**、**对局攻略退出衔接收敛至 PollCoordinator**、**设备配置页双形态（「模拟器 / 官方 PC 客户端」单选 + 品牌下拉五家 + 特性 ⓘ 悬停提示 + PC 形态窗口候选列表 + 品牌切换记忆）** |
| 9 | [巅峰赛与实战配队](./module_peak_combos.md) | `src/ui/match/peak_*` + `src/business/analysis/peak_ban_advice.py` + `src/business/recognition/peak_select_watcher.py` + `src/data/combo_*` + `src/ocr/card_grid_detector.py` | 巅峰赛（2v2）选将实时识别循环（**会话制互斥 + 会话世代校验 + 候选面板持续刷新治理 + 人工确认残留修复 + 过场坏帧扣住一拍复核**）、禁选建议象限判定、实战配队（combos）数据管理、座次解析与配队导入、**巅峰赛面板跳变与转场误退出治理（62c5c23）**、**排名量纲收敛与 stale 误清修复（c4a8cf4，`win_rate_ranking`）** |

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

## 本轮文档校准（2026-10-06）

自基线 `885ea96`（2026-10-02 校准）以来，共 30 个提交（10-03 至 10-06，至 `0ffed36`），本轮校准覆盖其全部变更：

**B1 OCR 全量切换**（1694ab7 / d1a55e1，本轮最大变更）
- **paddle 全栈退役**：`paddle_loader.py`（228 行）删除，由 `src/ocr/engine_loader.py`（248 行）替代；依赖从 paddlepaddle-gpu / paddleocr / protobuf + CUDA 系收敛为 **rapidocr==3.9.2 + onnxruntime==1.23.2 唯一 OCR 栈**
- **RapidOCR 双套件**：PP-OCRv6-small 主引擎（v6 三件套 rapidocr wheel 内置）+ PP-OCRv4-mobile 复核引擎（三件套经 `src/scripts/fetch_recheck_models.py` 官方清单预取，URL+SHA256 钉死、幂等、`.part` 原子替换）；`MUMU_OCR_PRIMARY_ENGINE=v4` 为回滚档且主复核角色互换；设备恒 CPU、线程钉 `MUMU_OCR_CPU_THREADS`（默认 6）、det 统一 max/960 与生产画布同口径；`RapidOcrEngine` 包装类将 RapidOCR 结果翻译为 paddleocr 2.x 风格（box 必须 `tolist()`）
- **白名单治理**：基线 7 对 → 12 对 → **10 对**（终态：昧→眜、半→芈、丰→羊、口→吕、好→妤、邻→郃、赢→嬴、苟→荀、早→卓、哈→哙）；简繁变体对退役（瓚↔瓒 0.94、黃↔黄 0.725 由视觉字形路径自动矫正，白名单回归"视觉评分不足 0.55 的形近临界对"本职）；新增 `effective_whitelist` property 与 `whitelist_conflicts_with_roster()` 武将库冲突重检谓词（doctor「白名单」检查组消费）
- **识别链适配**：单槽回退触发线 `_NAME_RECHECK_CONFIDENCE` 0.8 → 0.75（v6 置信分布定标，触发率 4.5%→1.7%）；对局 team 徽记归一化失败即主→复核引擎链式重读（救回 15/18 个 v6 书法体徽记丢标签槽）；预热单路径化（rec-only 残留删除，修复预热状态机误报 `warmup_failed`）；官方榜单导入罕见字兜底 chinese_cht 繁体引擎链退役，改由复核引擎在 `allowed_names` 候选闭包内复核
- **打包精简**：build_deps 机制删除、collect_all 缩至 4 包、excludes 恒排除 paddle 系（牵出 libpaddle.pyd 约 148MB 问题根除），精简包 763MB→**608MB**

**数据出库与私有数据仓**（7bdf075 / 8d23493 / 9958c79 / 80cc75b）
- 抓取数据（heroes/combos/cards/card_annotations/special_cards、raw_guides/、rag_corpus/、images/）与维护数据（card_points/equip_attrs/hero_classification/mjs_adjustments）自 2026-10-03 起移出版本库；公开仓只含源代码、测试与 `data/samples/` 虚构示例
- 新增 `src/scripts/pull_data.py`：pull / push / verify 三子命令 + `--dry-run`；私有仓位置三级解析（环境变量 `MJS_DATA_REPO` > config.env > 默认同级 `mjs_data_private`）；manifest.json sha256 校验；push 守卫链（数据有效性校验 `MIN_HERO_COUNT=100` + 脏工作区拒绝 + `git pull --ff-only` 对齐远端）；pull 原子落位（staging + `Path.replace`）+ 脏覆盖先备份 `data/backups/`（扁平命名）
- `data/SOURCES.md` 第三方内容来源登记表落地

**运维工具链**（b3327f1 / 1426686 / c88bf63 / 31f59be）
- `src/scripts/doctor.py`（新增 609 行）：11 组只读体检 + `--offline` / `--json`，FAIL 退出码 1；401 匹配收紧为 "HTTP 401"（af8cd06，消除 token 统计行误计）
- `src/scripts/ops.py`（新增 149 行）：weekly（pull→diff→maintain_rag→pytest→push，前置失败不 push）/ health / retry-failed / clean 四子命令
- `src/business/src/config/task_ledger.py`（新增）：任务台账 JSONL 落 `logs/task_results.jsonl`，五个写入点（base_fetch_service / indexer / maintain_rag / pull_data / ops）
- `src/config/version.py`（新增）：版本单一来源 `app_version()`；release.py 发版校验与发版清单 `logs/release-{version}.json` 终结三源并存的局面
- R2 十项：维护脚本 crash logger 补装、manager 坏文件保底副本、RAG 索引写后条数一致性校验、OCR 退役 worker 强杀兜底（先 `logging.shutdown()`）、threading.excepthook、首启资料部署标记 `data/.deployed`、QProcess 看门狗 30 分钟、缓存畸形行显式告警、sync_rule_stats 缺源升级 error、pytest basetemp 收口 `.tmp_test/pytest-tmp`
- 启动自动公告检查（每日一次、启动 2 分钟后、忙碌/冷却静默跳过）与启动截图回收（14 天）

**数据层健壮性与 AI 质量**（be40d26 / 9737d52 / d2c5b83 / 608ed62 / 1fbfce4 / b181e13 / 4720366 / f9f3b53 / cf9c927 / cfca666 / 0ffed36）
- `json_repository.py` 三基元：`atomic_write_json`（+`sort_keys`）/ `atomic_write_text` / `snapshot_to_backups(keep=10)`；全库 8 处固定名 `.tmp` 写盘收敛（根治并发互截）；`JsonRepository.load` 与 `card_catalog` 坏文件分支补保底快照（80cc75b）
- AI 生成入口与数据管理备份接入统一写前快照；Skill.description / settlement 禁止显式空串（`min_length=1`）
- 采集守卫：全量四道规模守卫（`refuse_write_reason`，空描述占比 >20% / 条数跌幅 >30% 等即中止）；增量空结果退出码改失败 + 损坏本地文件硬停
- httpx 超时分层化（connect/pool=5s 快速失败，read 保持配置值）并在重试重建时保持；`MAX_OUTPUT_TOKENS` 上调 32768；AI 定向重试 `--retry-failed`
- prompt 强化（cfca666）：相性评分四层决策门（score=协同增量）与锚例校准（10 对样本漂移 20%→0%）；攻略 JSON 纪律与联动判定强化（20 武将样本 JSON 抛错率 20%→0%）；guide 温度 0.7→0.55
- 设备离线计入闲置暂停（`RETRYABLE_CONNECTION` 不再清零闲置计数，ERROR 约 1800 条/小时→约 12 条/小时）；ComboStrip `ratings_computed` 改 `Signal(object)` 根治 int 键 dict 打穿 QVariantMap 的"实战 ★评级"角标静默失效（0ffed36）

**修正的失效描述**
- 代码规模台账：129 文件 / 1481 个 `test_*` 函数（原 112 / 1350）
- 技术栈：OCR 引擎为 RapidOCR/ONNX 双套件（v6 主 + v4 复核），paddle 已退役；`MUMU_OCR_USE_GPU` 转死键（doctor 登记），新增 `MUMU_OCR_PRIMARY_ENGINE`（默认 "v6"）与 `MUMU_OCR_CPU_THREADS`（默认 6）
- RAG 语料块数校验基线：武将语料/特殊机制语料改 `"snapshot"` 快照基线（快照值 647 / 85），加将自动适应、丢块仍拦截
- 免责声明版本 1.1；VERSION 文件 1.2.1；打包体积 608MB（原 834MB 基线）
- "B2 复核模式"提法更新为"复核引擎"（B1 起复核为 v4，与主引擎互为异构，回滚档时互换）

## 本轮文档校准（2026-10-11）

自基线 `0ffed36`（2026-10-06 校准）以来，共 18 个提交（10-07 至 10-11，至 `2081b5b`），本轮校准覆盖其全部变更：

**RAG 编码器 ONNX 化**（2081b5b，本轮最大变更）
- **编码器抽象层** `src/rag/embedder.py`（新增 175 行）：`OnnxEmbedder`（onnxruntime CPU + `tokenizers` 直读 tokenizer.json，CLS 池化 + L2 归一化，max_length 512 右 padding，输入名以 sha256.json 侧车记录为准，构造即校验三件套哈希、缺失/不符即 `RuntimeError` 熔断并带修复指引 `python -m src.scripts.export_embedding_onnx`）+ `STEmbedder` 回滚档（原 sentence-transformers 行为原样保留）+ `get_embedder()` 按 `RAG_EMBEDDING_BACKEND`（默认 `onnx`，可设 `st`）单例派发；encode 契约不变量（单字符串一维 / 列表二维 `(B, D)` float32）
- **检索/建索引链路切换**：retriever 与 indexer 的模型加载统一走 `get_embedder()`；运行期不再 import torch/transformers/sentence-transformers
- **ONNX 导出链** `src/scripts/export_embedding_onnx.py`（新增 250 行）：opset 18 + 动态轴（batch/seq）、两条不等长校准样本（防 dynamo 把 batch/seq 维专门化）、`do_lower_case` 按 `sentence_bert_config.json` 对齐 + `strip_accents=False`、权重内联回单文件、`--int8` 追加量化（S7 裁决维持 fp32 打包，int8 不入包）、侧车写 `sha256/bytes/input_names/source_weights_sha256`、幂等跳过、`--check` 只校验
- **索引重建语义修复**：`build_index(rebuild=True)` 由 `delete_collection` 改为整目录 `shutil.rmtree(config.CHROMA_DIR)` + 新建，孤立段残留根除（实测索引体积 80.9MB→14.8MB），删除失败 `RuntimeError` 中止不静默
- **检索 trace 可观测**：`Retriever.search(trace=...)` 记录五种丢弃原因（below_threshold / not_current / hero_filter / max_keyword_only / kind_quota），默认 None 零开销行为不变；`python -m src.rag.retriever --query … --explain` CLI 输出候选去向
- **检索诊断抽屉**：`src/business/rag/retrieval_diagnostic.py`（`diagnose` 纯 dict + `DiagnosticWorker` QThread，`LIVE_WORKERS` 僵尸防护）+ `src/ui/maintenance/retrieval_diagnostic_dialog.py`（9 列候选去向表、top_k 下拉、backend/耗时/保留丢弃计数、排序开启下填充前禁排序防串行）+ 知识库维护页「检索诊断」按钮（只读，关闭不触发 refresh）
- **评估历史**：`src/scripts/rag_eval_history.py`（新增，`logs/rag/eval_history.jsonl`，记录 backend / 模型指纹 / 语料块数 / 索引体积）；`eval_rule_faqs` / `eval_guide_quality` 每次运行默认追加、`--no-record` 逃生，退出码契约不变
- **doctor 新增「RAG索引」检查组**（11 组 → 12 组）：索引目录存在性、体积 ≤25MB、集合段目录唯一性、ONNX 三件套哈希（st 档 SKIP）
- **打包形态**：精简版自带完整 RAG（chromadb collect_all + data/rag_index + data/rag_models/onnx 三件套移出 FULL 块；st 档 modelscope 权重不再收集）；excludes 移除 `tokenizers`、新增 `scipy/sklearn/pandas/matplotlib/hf_xet`（回收约 114MB）；spec 顶部 ONNX 前置断言（拦截绕过 release.py 直接打包）；FULL 块只剩 playwright；两包实测 658.9/764.1MB（发版断言 670/800MB）
- **发版四连门禁**（release.py +196 行）：ONNX 就位（缺则自动导出）/ 强制 onnx 后端同源重建索引 + 块数一致性断言 / 包内产物校验（发现 int8 即 die）/ 包内 eval 检索门禁；manifest 新增 `encoder_backend / onnx_model_sha256 / index_blocks / unpacked_mb / eval_gate`
- **环境依赖**：environment.yml 新增 `tokenizers==0.22.2`（运行期）、`torch==2.12.0` / `onnx==1.23.2` / `onnxscript==0.7.2`（导出链 dev）；sentence-transformers 降级为回滚档与导出源
- **配置键**：新增 `RAG_EMBEDDING_BACKEND`（默认 "onnx"）、`ONNX_MODEL_DIR`（`data/rag_models/onnx`）、`RAG_EMBED_THREADS`（默认 4，onnxruntime intra_op 线程）

**数据新鲜度芯片**（ae92c1d）
- **判定核心** `src/business/freshness.py`（新增 449 行，无 Qt 依赖）：档位周期写死 [0,7) 绿 / [7,14) 黄 / ≥14 红；`compute_freshness_report()` 判定武将/攻略/相性/榜单四项——武将项**逐事件逐武将核对**（事件落后 ⇔ 武将缺失或 `last_updated` 早于事件日期，批次按公告 ref 去重），修复旧全局 max 对比下「指定获取洗绿」「混合批次只推增量致调整武将漏掉」「攻略/相性部分覆盖洗绿」「部分攻略缺失未暴露」四缺陷（5 个缺陷复现用例先行转绿）；建议路由 `SUGGEST_INCREMENTAL`（缺失→增量获取）/ `SUGGEST_SPECIFIC`（过期→指定获取），混合批次双给；时间轴不健康（缺失/损坏/超阈值）退化为纯年龄兜底并出 `timeline_warning`；精简版 `full_build=False` 只判武将+榜单
- **状态栏芯片** `src/ui/app/data_freshness_chip.py`（新增 201 行）：第三枚常驻胶囊，六路服务信号 + QFileSystemWatcher（data/ 目录，mtime 快照 500ms 去抖，过滤无关文件）双路刷新、无轮询定时器；点击/键盘弹「内容年龄 / 数据源周期 / 建议操作」三组菜单，建议动作直达 DialogCoordinator 采集入口（`hero_fetch_service.is_busy` 时 toast 拦截）；配置键 `DATA_FRESHNESS_TIMELINE_STALE_DAYS`（默认 14，透传键不进 UI 配置对话框）

**竞态守卫原语定稿与全仓收口**（8a08d5d / 4542244 / 89e4e8d / 76875dd / df019a5，全部语义不变的纯收口）
- `src/business/recognition/session_guard.py` 三原语：`SessionGuard`（世代计数器，快照—校验模式）、`BusyGate`（忙碌闸，acquire 锁内检查+置位原子、release 幂等）、`OneShotToken`（一次性令牌，置位不可复位）
- 收口点：OCR 在途轮询拍 `_poll_in_flight` → `ocr_service._poll_gate`（BusyGate）；公告更新阶段令牌 `_phase_in_flight` → `announcement_update_coordinator._phase_gate`（BusyGate）；模拟器操作服务关停旗标 `_closed` → `_shutdown_token`（OneShotToken，emulator_operation_service 与 capture_service 两处）；巅峰赛会话世代 `_session` 裸自增 → `peak_select_watcher._session_guard`（SessionGuard）+ 三处手抄重置统一 `_clear_session_state(keep_signature/keep_miss_ticks)`
- 语义单测 `tests/test_session_guard.py`（5 用例，含 8 线程争用单赢家）

**CaptureService 连接域出仓**（118f59c，纯内部等价重构）
- 新文件 `src/business/emulator/capture_connection.py`（191 行）：`CaptureConnectionManager` 承接配置热更（update_config 三键比对重建 AdbCapture / set_target_port）、会话状态（set/sync/sync_poll，过期实例忽略）、连接编排（connect/disconnect，阻塞 IO 全在 `_adb_io_lock`）四大职责；两把锁（`_session_lock` 护内存态 / `_adb_io_lock` 串行 ADB IO）不嵌套持有；状态与消息经构造注入回调广播（emit 留在 QObject 宿主）
- `capture_service.py` 575→479 行，连接域全部变一行转发，关停旗标同步 `OneShotToken`；信号与对外 API 零改动
- 连带修复（6ea1866）：`mumu_config_coordinator.shutdown()` 显式断开 `connection_changed` 中继连接（emit 形式中继无 receiver 不随销毁解除，残留连接对已销毁对话框抛 "Signal source has been deleted"）

**OCR 识别链重构**（2646fd4 / b5e8e4f / 6ea1866）
- `_recognize_page` 页型模板方法：`_PagePlan` 冻结数据类四开关（has_team / short_circuit_name_recheck / emit_empty_results / log_details）+ 两模块级单例（`_WUJIANG_SELECT_PLAN` / `_MATCH_GUIDE_PLAN`），recognize 双页型 90 行平行路径合一（519→493 行，纯等价重构）
- match_guide 兜底 OCR 批次零产出即短路：名称批次画布无文本行时跳过逐槽双证据与阵营逐槽重试（"读出字但归一化失败"仍重试），错位页面每轮 det 调用约 14 次 → 4 次；`RapidOcrEngine` 新增 `reset/drain_call_stats()`，阶段耗时日志新增"空检=n/m"聚合摘要，`_EmptyDetFilter` 幂等滤除逐条空检告警
- `ocr_worker.py` 七个阶段耗时标量收口为 `stage_timing` 字典逐段累积，5 处 `_log_timing` 调用点 `**` 展开（日志输出逐字节不变，551→518 行）
- `name_resolution.py` 识别状态词表单源三常量：`CONFIRMED_RESOLUTIONS`（已确认集转公共）/ `UNRESOLVED_RESOLUTIONS`（未定全集）/ `ACTIONABLE_UNRESOLVED_RESOLUTIONS`（收窄集，unknown 拒识无材料有意收窄）；ocr_worker 待确认统计 / peak_select_watcher 人工确认 / recognizer 复核注入三处手抄副本改引常量

**巅峰赛坏帧拦截**（036d0ae，行为修复）
- `peak_select_watcher.py` 新增 `_deferred_board` 机制：选将/禁选期真实池子单调不增前提下，新拍卡数严格高于上一发布拍即判疑似过场坏帧（2026-10-09 事故：场景切换期 raw 截图返回旧 9 张帧致面板 8→9→8 跳变），扣住一拍待复核——紧邻下一拍签名复现即补发布，否则按坏帧丢弃；隔拍再现因回升规则仍在会重新扣住；牌面缺席拍作废扣住状态；`_clear_session_state` 同步清 `_deferred_board` 不跨会话存活；卡数下降（禁转选 14→9）与同数重排不受影响照常立即发布

**打包态读写根分裂治理**（6b21d75）
- 概念：frozen 下 `BUNDLE_ROOT`（`<exe>/_internal`，只读）与 `PROJECT_ROOT`（exe 同级，可写）分裂，缺陷形态是"写入可写根、读取钉死只读根"；治理方向为**读取侧统一到运行时根**，首启由 `_ensure_clean_runtime` 从打包默认部署可改副本（只补缺失）
- 换根清单：三份榜单 CSV / 推荐指数与武将 JSON 常量（win_rate / peak_win_rate / recommendation_index 三仓储）、AI 批量输出 `DEFAULT_DATA_DIR`、全量/增量默认输出、`_CORE_RULES_FILE` 核心规则摘要、`DEFAULT_PRICING_FILE`、`FACTION_COLORS_FILE`；头像新增 `portrait.find_portrait_path()` 两级回退（运行时根 IMAGES_OUTPUT_DIR → 随包基线 IMAGES_DIR，扩展 .png/.jpg/.webp），`HeroCardWidget._load_portrait` 接入同源（保留 KeepAspectRatio 留白式，与共享 load_portrait 的 ByExpanding 裁剪式为刻意差异）；`images/` 不纳入首启部署（避免安装体积翻倍）
- `main.py._ensure_clean_runtime` 新增部署 `config/faction_colors.json` / `config/model_pricing.json` 可改副本
- 修复的打包态行为：OCR 导入新榜单后算法读旧基线、抓取与生成结果 UI 不可见、自定义配色/价格随升级丢失、Program Files 下保存失败、新下载头像永不显示
- 新增守护测试 `test_ranking_csv_runtime_root.py`（monkeypatch 双根 + importlib.reload 模拟分裂）与 `test_runtime_root_config_and_portrait.py`

**其他**
- **env.py 键元表单源化**（1bfd65b，纯重构行为零变化）：`_EnvKeySpec` NamedTuple + `_ENV_KEY_SPECS` 注册表（现 30 键：runtime 6 + mumu 15 + 透传 9）唯一事实源，`load_env_config` 按 spec.type 统一转换、`default_from` 表达级联默认（`mumu_hero_selection_threshold` 未配置回退 `mumu_ocr_match_threshold` 实配值）；`DEFAULT_MAX_OUTPUT_TOKENS = 32_768` 单源（api_generator 引用同对象）；配套 `_SENTINELS` 全量键哨兵表测试
- **AI 双生成器后处理管线收口**（cd97677，纯重构）：`src/scraper/ai/utils.py` 新增 `normalize_and_validate_guide / normalize_and_validate_synergy` 共享管线（注 ID / int 转换 / `combat_synergy → combo_ceiling` 兼容 shim / 必填预检 / Pydantic 校验），api/browser 双生成器各 ~10 行内联缩为一行调用；`logger_prefix` 参数保留浏览器版日志前缀（进度窗口解析依赖）
- **忽略差异列表骨架收口**（cd97677，纯重构）：新文件 `src/ui/data_admin/ignoreable_diff_list.py`（108 行）`IgnoreableDiffListMixin`，收口 CardSyncDialog 与 HeroUpdateConfirmDialog 逐字复制且已偏移的忽略入口/详情对比/选中联动骨架；偏移语义提升为一等钩子（hero 版 content_hash 门控 + 无服务时整组隐藏，card 版选中即可忽略；`_detail_dialog_class` 钩子避免循环导入）
- **实战配队批量生成对话框按配队武将筛选**（1a765da）：`SynergyCombosDialog` 构造签名新增必选首位 `hero_manager`（对齐同族传参惯例），可编辑下拉 + QCompleter 包含匹配；按 hero_id 精确匹配两个位置（免疫重名与孤儿 id），与评级/座次/生成状态筛选叠加
- **UI 样式**（b5e8e4f）：`style.py` 新增 `_section_tabs_qss(object_name)` 参数化提取，资料库二级页签与页面内分区页签共用的四条 QSS 规则单源（GLOBAL_STYLE 渲染逐字节一致，守护测试 `test_style_qss.py`）
- **pull_data 编码修复**（2945f29）：`_git()` 显式 `encoding="utf-8", errors="replace"`——中文 Windows 控制台无 PYTHONUTF8 时 git 输出（恒 UTF-8）按 GBK 解码致读取线程 UnicodeDecodeError（git 本身已成功但输出丢失）
- **静态汉字特征缓存回填**（6b21d75 chore）：`src/data/char_info_cache.json` 424 → **437** 字
- conftest 新增 autouse 模态守卫 `_fail_on_real_modal`（QMessageBox 五个静态便捷方法在测试中直接 AssertionError，根除无头环境真实模态 exec() 永久阻塞致 xdist worker 被强杀的失败漂移）

**修正的失效描述**
- 代码规模台账：145 文件 / 1626 个 `test_*` 函数（原 129 / 1481）
- 技术栈：RAG 编码器为 onnxruntime 默认 + sentence-transformers 回滚档（`RAG_EMBEDDING_BACKEND`），运行期不依赖 torch 栈；doctor 检查组 11 → 12（新增「RAG索引」）
- 打包体积：精简 608MB → 658.9MB（自带完整 RAG + ONNX 编码器）、完整 764.1MB（发版断言 670/800MB）
- `CaptureService` 行数 570 → 479（连接域出仓后）；`peak_select_watcher.py` 棘轮 566 → 593（坏帧拦截）；`ocr_worker.py` 棘轮 551 → 518（stage_timing 收口）；`recognizer.py` 棘轮 519 → 493（模板方法）

## 本轮文档校准（2026-10-23）

自基线 `2081b5b`（2026-10-11）以来共 40 个提交（含 50681fd 文档提交本身），本轮校准覆盖其全部变更。新增 11 个文件，新增 1 份规格文档（`docs/spec/spec_onboarding.md`，446 行）。

**首启向导三段体检**（6962151 / 12c7efb）
- `src/config/onboarding_state.py`（新增 96 行）：`config/.onboarding_state.json` 三段状态（`ONBOARDING_VERSION="1"`，`steps` 记录 env/device/recognize 完成时刻），状态文件缺失/损坏/版本不符一律按"未完成"处理（与 disclaimer_state 同容错口径），`skipped=True` 后不再自动弹出
- `src/ui/app/onboarding_dialog.py`（新增 551 行）：一页式三段——环境自检 → 模拟器连接与分辨率体检 → 试识别；每段失败给出唯一可点修复动作；合规边界写死在模块 docstring（只做只读检查、ADB 读屏、OCR 识别，界面明示"本工具只读屏幕，不操作游戏"）
- `src/ui/app/onboarding_fixes.py`（新增 45 行）：`OnboardingFix` / `OnboardingFixResult` 修复动作库
- `src/main.py` 接线：`show()` 后按 `should_show_state()` 弹向导（预热已返回、事件循环尚未接管）；`MJS_ONBOARDING_FORCE=1` 强制在预热前弹出（发版 smoke 用，此时先 `splash.close()` 避免 StaysOnTop 遮挡）
- 帮助菜单新增「重看首启引导」；`12c7efb` 修复深链端口倒退与启动异常保护

**爬虫合规加固与访问披露**（9180cf0 / 29ae6d6 / a78e801）
- **如实爬虫标识**：官网采集 UA 由浏览器伪装改为 `mjs-agent/1.0`（附项目地址），`fetch_recheck_models.py` 用 `mjs-agent-fetch`
- **robots.txt 规则执行**：随抓取存档（24 小时内不重复请求）+ 抓取前按已解析规则校验目标 URL，`Disallow` 路径拒绝抓取（公告接口与图片域名按各自 robots.txt 校验；获取失败放行并告警）
- **生成任务互斥锁** `src/scraper/ai/generation_lock.py`（新增 104 行）：OS 级文件锁（Windows `msvcrt.locking` / POSIX `fcntl.flock`），持有者 pid+时间写旁路 `*.holder` 文件（Windows 字节锁会拒绝其他句柄常规读取）；锁文件不可用时降级哑锁放行（`fd=-1`）保持无锁旧行为
- **官网字段清洗** `src/scraper/official_source/hero_fields.py`（新增 64 行）：技能描述解析兼容官网异常标题格式，写入守卫拦截解析丢失覆盖
- **自动化访问披露**：TERMS.md 新增第 7 节（Playwright 附加 `--disable-blink-features=AutomationControlled`、相邻两次生成随机休息 60~180 秒、如实风险承担），版本 1.1 → **1.3**（修订日期 2026-10-19）；`DISCLAIMER_VERSION` 1.1 → 1.3；TERMS.md 原第 8/9/10 节顺移为 9/10/11 节

**抓取链路健壮性**（f1b13f8 / 14bffaf / 07606ce / f10b477 / 1d8334a / 2b73535）
- `ScriptRunner` 启动失败终态契约：Qt6 对 `FailedToStart` 只发 `errorOccurred` 不发 `finished`，`_on_error_occurred` 统一收尾（停看门狗 + `deleteLater`）；`FAILED_TO_START_EXIT_CODE = -1`；`_start_pending` 区分 Windows 同步失败（返回值 `False` 表达，不发 `finished`）与 Linux 异步失败（补发 `finished` 兑现"返回 True 必收到一次终态"）——封堵面板永久忙死（VUL-B2-001）
- **看门狗树杀**：`_kill_process_tree()`（Windows `taskkill /PID /T /F`，子脚本拉起的浏览器/同步进程不再孤儿化）+ `WATCHDOG_RECHECK_MS = 2000` 延迟复查（仍运行则直接 `proc.kill()`）；`Starting` 状态下 `kill()` 会在事件循环内原生 abort，改由 `started` 信号到达后补杀；复查绑定具体 proc 而非读 `self._proc`（避免误杀后续新任务）
- **看门狗无进度上限**（f1b13f8）：长清单浏览器模式任务不再被 30 分钟绝对超时误杀
- **取消链路**：`BaseFetchService.cancel()` 在 `Starting` 阶段只置标记、由 `started` 信号到达后再树杀；清理轮询加上限；`_cancel_cleanup_process` 防悬挂引用
- **主窗口退出**：`closeEvent` 新增 `shutdown_fetch_services()`——QProcess 随父对象析构不杀子进程树，浏览器模式会孤儿化 Edge/node 并锁住 `data/edge_profile`，下次启动报 locked（VUL-B2-002）
- 抓取域：空目标伪成功丢数据修复、AI 批次中断状态、浏览器会话超时自动重建、官网清洗/缺 id 健壮性、相性重载双向互斥

**数据安全与备份恢复**（062071a / 0fff5e2 / 9ccc2fc / 3a8845f / f206eed / cf923f4）
- **三条数据全灭路径封堵**：修复清空、导入覆盖、回滚误导三条路径（`062071a`）；预热弃单锁死、卡牌告警累积、超时不重载与攻略读取降级（`0fff5e2`）；数据回滚、子进程工作目录、进程释放与忙碌闸语义分立（`9ccc2fc`）
- **坏文件留底第四基元** `preserve_corrupt_file`：JsonRepository 与 DataManager 双路径留底（`_corrupt_preserved` 幂等标志 + `from_disk` 参数语义），坏结构先留底再降级，不静默丢弃
- **`save_payload()` 加载完整性守卫**：保存前校验对象来自完整加载，阻断半加载对象覆写正式数据
- **备份恢复消费端**：`src/business/maintenance/backup_restore_service.py`（新增 247 行）+ `src/ui/data_admin/backup_restore_dialog.py`（新增 410 行）——列出/预览/恢复 `data/backups/` 自动快照；恢复前先 `snapshot_to_backups()` 快照当前状态（双向可回退）；原子写替换失败不留半截文件；坏备份（结构损坏或含校验失败记录）在预览与恢复两侧都拒绝；跨文件引用（武将/相性/攻略）默认整组恢复到同一时间点；「数据 → 从备份恢复」入口
- **导入前备份与源结构校验加固**（3a8845f）；公告域快照落盘兜底与忽略名单名字兜底（`801c9f1`）；推荐指数 stale 判定纯读化（`>=` 且不再写回状态）

**相性指定获取双入口拆分**（2f4a966 / c4d547a）
- 「指定配对计算」（`SynergyPairDialog`）：按状态筛选武将后按策略组装精确配对清单
- 「落后相性刷新」（`src/ui/generation/synergy_refresh_dialog.py`，新增 200 行）：列出已有但落后于武将资料的配对，按最后生成日期升序，勾选后仅刷这批
- 菜单重命名：原「武将相性 → 指定获取」拆为「指定配对计算」+「落后相性刷新」

**巅峰赛与分析域**（8020da8 / 62c5c23 / c4a8cf4 / 67a67ef）
- 选将卡位逐拍漂移抑制与巅峰赛面板跳变治理；牌面漏检引发的面板跳变与转场误退出治理
- 分析域重构：`src/business/analysis/win_rate_ranking.py`（新增 31 行，排名/量纲收敛）、stale 误清修复、失败态伪装修复
- 对局攻略退出衔接收敛至 `PollCoordinator`，删除 watcher 侧恢复双头

**其他修复**
- **模拟器域**（a2f6ecb / e790a9f）：OCR 关停复活竞态、轮询重连漂移、等待上界、截图异常静默卡死、首启体检槽位泄漏；`src/business/emulator/layout_resolution.py`（新增 52 行）
- **识别域**（4e0cafe / cb2b47d）：白名单缓存失效、轮询异常兜底、超时任务弃单释放引用、发布世代守卫、落盘降级、罕见字计数熔断、取消静默
- **公告域**（788e297 / d5cceeb）：启动即加载公告管理器（已处理公告重启后不再反复提示待更新）、官网访问冷却限频与并发守卫；`src/business/access_cooldown/check_cooldown.py`（新增 106 行）
- **业务根域**（1dc08d5）：新鲜度损坏留底与阈值可配置、注解保存回滚、冷却闸门收口
- **RAG 域**（2f4a529）：审计容错、建议线程并发收口、语料写前快照与用量补记
- **成本与卡牌**（87eac8d）：ai_cost 估算口径修正、card_sync 应用链路数据完整性

**新增配置键与规格**
- env 键元表 `_ENV_KEY_SPECS` 30 → **32 键**：`DATA_FRESHNESS_STALE_GREEN_DAYS`（默认 7）、`DATA_FRESHNESS_STALE_RED_DAYS`（默认 14）——官方榜单约每半月更新，7/14 写死会让榜单长期停在黄/红并盖掉武将信号（TD-A6-001）
- `docs/spec/spec_onboarding.md`（新增 446 行）：首启向导目标/验收口径/合规边界规格

**修正的失效描述**
- 代码规模台账：153 文件 / 1936 个 `test_*` 函数（原 145 / 1626）；`src` 共 274 个 py 文件 / 56123 行
- 免责声明版本 1.1 → 1.3；TERMS.md 1.1 → 1.3（新增第 7 节自动化访问披露，原第 8/9/10 节顺移为 9/10/11 节）
- env 键元表 30 → 32 键；`rule_doc_panel.py` 行数预算 927 → 935
- 数据规模（当前私有数据仓同步快照）：heroes 192 / combos 1827 / cards 49 / special_cards 85 / equip_attrs 26 / hero_classification 7 键（categories 16 + hero_categories 192）/ mjs_adjustments 150 事件 / char_info_cache 随包基线 437 字（用户层覆盖 135 字）

**文档结构变化**
- `docs/call_graph/` 自 50681fd 起**退出版本库**（`.gitignore` 第 122 行 `docs/call_graph/`，注释"函数调用链文档（个人人工 review 参考，不入版本库）"），仅保留在工作树供人工 review；本次仍按用户要求同步更新其内容

## 本轮文档校准（2026-10-25）

自基线 `a63bc3c`（2026-10-23）以来共 12 个提交（10-24 至 10-25，至 `ed8b4ac`），本轮校准覆盖其全部变更。新增 `src/capture/pc_client.py`（577 行）与 `src/capture/emulator_discovery/` 包（9 文件，其中 `mumu.py` 自原单文件探测模块平移、其余 8 个为新文件），新增 3 份设计/需求文档（`docs/design/emulator_multi_brand_requirements.md` / `pc_official_client_capture_design.md` / `pc_official_client_requirements.md`）。

**多模拟器五品牌连接**（8f2e70a / feee42b / 514b4f8 / 4eb3445 / 282ed5b）
- `src/capture/` 原单文件探测模块拆包为 `src/capture/emulator_discovery/`（10 文件）：函数式注册表 `EMULATOR_DISCOVERY_MODULES`（每品牌统一契约 `discover_devices(retries)` / `find_adb()`，不引入 Protocol）+ `models.py` 品牌无关值对象 `EmulatorDeviceInfo` + `port_scan.py` 端口段兜底（8 并发 socket 试连，`PORT_RULES` 品牌端口带，结果标注"品牌未验证"防 5555 共用端口误标）+ `console_utils.py`（console 工具 UTF-8→GBK 解码 + `find_process_dir` 进程反查安装目录）
- 五品牌探测器：MuMu（MuMuManager JSON）/ 雷电（ldconsole list2 + 5555+2n 推导）/ 夜神（NoxConsole list 七列 + netstat VM PID 精确关联 62001+24n）/ 逍遥（`.memu` 配置直读 ADB 转发 hostport，console 为 GUI 无 stdout 死路径已移除）/ 蓝叠（注册表 BstAdbPort → bluestacks.conf，国内版 5.22 适配）
- `AdbCapture` 品牌参数化（`emulator_type` 构造参数）+ `connect(validate_capture=)` 连接后截帧校验 + `_reconcile_emulator_serial()` 雷电 adb 主动扫描区 `emulator-<n>` 序列号双查
- 探测提速（真机计时）：逍遥 10.5s → 0.08s（console 死路径移除 + 同键任务去重）、雷电/夜神 3.8s → 0.6s（进程反查 tasklist 门控 + PowerShell 合并查询；端口扫描并行化）；五品牌全部 <1s
- 配置键 `MUMU_*` → `EMULATOR_*` 平滑升级（`default_from` 回退旧键，持久化始终写新键）+ `EMULATOR_BRAND` 品牌记忆 + `PC_WINDOW_TITLE`，env 键元表 32 → 38 键

**官方 PC 客户端直连**（ed8b4ac）
- 新增 `src/capture/pc_client.py`（577 行）：`mjs.exe` + `UnityWndClass` 双条件窗口枚举、三态解析（未启动 / 单窗自动选定 / 多窗显式选择，不支持多开）、Windows.Graphics.Capture 主路径（`windows-capture==2.0.1`，Win10 1903+，遮挡可采集）+ GDI BitBlt 兜底 + 黑帧双指标检测引导；WGC 会话窗口句柄定向（标题子串匹配命中工具自身窗口的事故根治）、帧尺寸对账防线、BGRA→RGB 通道反转、Per-Monitor-V2 DPI 声明硬前置；模块全程只读窗口画面不发送任何输入（法律红线）
- 连接层双形态：`CaptureConnectionManager` 按 `emulator_type=pc_client` 构建 `PcClientCapture`（窗口标题变更或跨形态切换时重建，PC 形态不要求 ADB 配置），连接/断开事件文案按形态分立；`CaptureService` 采集会话类型注解统一放宽为 `AdbCapture | PcClientCapture`
- UI：设备页顶部「模拟器 / 官方 PC 客户端」单选（程序装载不产生副作用），PC 形态隐藏品牌行与 ADB 区块、设备下拉复用为窗口候选列表（同步枚举三态填充、720p/900p 低分辨率档位放行附非阻断提示）；切到 PC 客户端前记忆模拟器品牌、切回恢复（`EMULATOR_BRAND`）
- 环境依赖新增 `windows-capture==2.0.1`（environment.yml / environment-ci.yml）

**官方榜单导入提速**（366303c / 28fa11b / 8c0f871）
- 拼接长图兼容：`official_board_parser.split_stitched_image()` 把 20261022 拼接长图按静默带切分为补边虚拟分页（std<8、带长≥40px 区分旧版长图行隙 15~19px；段结构校验每栏 ≥30 行拦截头部图例区伪段），导入服务切分归一化后复用既有分页链路，坐标经段 y 偏移还原
- 单格识别快通道：`official_data_import_service` 新增 2× 放大单候选先试路径（`_fast_numeric_cell` 数字格需纯排名/胜率格式、`_fast_name_cell` 名称格需词表精确命中且置信度 ≥0.90），未过门原样回退 4× 双候选路径，精度下界不变，整板导入提速约 4.6 倍
- 待复核行截图改为仅保留武将名称格（`_row` → `_name_cell`），去掉无关整行画面

**杂项修复**
- **控制台日志队列化**（79fc4a0）：`logging_config._QueuedConsoleHandler` 把控制台写移出调用线程（Windows 经典控制台快速编辑选中会无限期阻塞 WriteConsoleW，2026-10-24 事故：GUI 主线程在 logger.info 上悬死）；文件 handler 仍同步落盘
- **公告冷却窗口分立**（39ec62b）：`AnnouncementService` 公告检查与获取武将变更各持独立 `CheckCooldown`（公告检查后首次获取武将变更不再受牵连，各自连续触发才限频），新增 `prepare_cooldown_remaining` 属性
- **抓取进程重入保护**（4bd9586）：`BaseFetchService._on_finished` 记录完成时进程引用，钩子经直连信号同步发起下一阶段（公告多阶段更新链）时不再误释放新 QProcess（消除退出码 0xF291 强杀）
- **爬虫超时解耦**（79fc4a0）：robots.txt（5s）与头像下载（15s）从主数据 30s 超时中解耦，站点异常时快速降级
- doctor 设备检查适配多品牌：读取 `emulator_adb_path` 新键、`adb devices` 序列号匹配放宽为 `127.0.0.1:<port>` 或 `emulator-<n>`、空结果修复文案按品牌分立（蓝叠提示开启 ADB）

**修正的失效描述**
- 代码规模台账：156 文件 / 2042 个 `test_*` 函数（原 153 / 1936）
- 技术栈：屏幕采集新增 `windows-capture==2.0.1`（PC 客户端 WGC）；env 键元表 32 → 38 键
- 设备探测为五品牌注册表 + PC 客户端窗口枚举（原"MuMu 实例探测"）；`AdbCapture` 接口对齐 `PcClientCapture` 双形态
