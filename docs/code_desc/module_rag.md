# 模块：RAG 知识库（语料 / 向量索引 / 混合检索 / 索引精化 / 元规则维护）

> 对应目录：`src/rag/`、`src/business/rag/`、`src/business/maintenance/`（RAG 三文件）、`src/ui/maintenance/`、`src/scripts/`（语料构建与维护脚本）
> 代码基线：commit `77e9407`（2026-09-04）
> 职责：维护游戏规则的三层语料资产，构建本地向量索引，向 AI 生成注入检索到的规则依据，并提供一套人工维护工作台

---

## 通俗概述

这个模块做的事情，可以类比成一个"游戏攻略编委会"：

1. **收集原始资料**。游戏官方的武将、卡牌、规则文档是"权威原文"，社区博主写的攻略和配队心得是"参考资料"。它们被原样存起来，不做改动。
2. **整理成便于查找的小卡片**。把大段原文拆成一块一块的"知识卡片"，每张卡片除了正文，还额外标注了"什么时机生效""触发条件是什么""该搜哪些关键词""跟哪些规则相关"。这些标注一开始由程序按固定规则抽取，抽不全的再交给大模型或人工补齐。
3. **建一个能"按意思搜"的目录**。每一张卡片被转换成一组数字（一段文字变成一串数字），存进本地向量数据库。用户用大白话提问时，就能按意思相近的程度找到最相关的卡片，而不只是按关键词匹配。
4. **让 AI 有据可依地写作**。当软件批量生成武将攻略或武将相性评分时，先把最相关的卡片挑出来塞进提示词，AI 就能照着官方规则写，而不是凭想象编。
5. **提供维护工作台**。软件里有一整个"知识库维护"页面：可以看到每类资料是否需要重新生成、哪些数据源有问题、哪些卡片的标注还空着（叫"待精化"）、官方的规则母本文档有没有和数据对不上。发现问题可以一键跳转过去修。

分层上遵循数据仓库的三段式：原始资料（贴源层）→ 加工后的检索语料（明细层）→ AI 生成的攻略与相性（集市层）。集市层只写不读，绝不反哺回明细层——因为那是 AI 的产物，不是权威资料。

---

## 一、模块职责

本模块承担六个角色：

1. **语料资产分层管理** — 把数据文件按来源性质与加工程度归入贴源层（ODS）、明细层（DWD）与集市层（mart），并保证集市层不被回读加工
2. **语料构建管线** — 10 个生成脚本从权威原文产出 12 个语料 JSON 文件，全部由 `src/business/rag/task_defs.py` 的 `TASKS` 统一定义，调度脚本 `maintain_rag.py` 按文件指纹增量重跑
3. **向量索引基础设施** — 语料块规范化为文本、本地嵌入模型（bge-small-zh-v1.5）向量化、写入 ChromaDB 持久化集合
4. **混合检索** — 元数据硬过滤 + 向量相似度 + 关键词兜底，经 RRF 排名融合与语料类型配额产出最终召回块
5. **RAG 注入 AI 生成** — 为攻略/相性生成提供检索块与提示词预算控制（实际提示词拼装归 `module_ai_batch.md`，本模块只到 `src/rag/retriever.py` 的召回边界）
6. **人工维护工作台** — 五个数据源编辑面板 + 元规则 T0 母本维护 + 索引精化三层架构 + 结构化审计驱动跳转

核心设计原则：**任务定义单一事实源**（`task_defs.py`）、**写盘一律原子**（委托 `src.data.json_repository.atomic_write_json`）、**UI 不持有业务逻辑**（清单归属、基线判定、线程编排全部下沉业务层）。

---

## 二、文件结构与分层对应

本章给出"文件属于哪一层"的完整归属表。分层判别标准是**来源性质 + 加工程度**，不是"是否被检索"——`data/rag_corpus/` 虽然被检索，但它是权威原文的理解整合产物，属明细层不属集市层。

### 2.1 分层模型

```
ODS（贴源·官方权威母本）
  │  build_*_corpus.py 加工
  ▼
DWD / DWS（加工语料，进向量库）
  │  混合检索召回 → 注入 prompt
  ▼
mart（AI 生成产物，只写不读，绝不反喂 DWD）
```

- **ODS（Operational Data Store，贴源层）**：官方原文原样入库，不做加工。改动即需人工操作。
- **DWD（Data Warehouse Detail，明细层）/ DWS（Data Warehouse Summary，汇总层）**：经脚本加工成检索块，可整体重建。
- **mart（Data Mart，集市层）**：AI 生成的最终产物，由 RAG 增强后重新生成覆盖。

### 2.2 ODS（贴源层，权威原文）

| 文件 | 说明 |
|------|------|
| `data/heroes.json` | 武将与技能权威源。磁盘实测 180 武将 / 442 技能 |
| `data/cards.json` | 官方卡牌 49 张 |
| `data/card_annotations.json` | 卡牌追加内容（与 `cards.json` 同为权威侧） |
| `data/card_points.json` | 卡牌点数花色：72 行牌面明细，`count` 合计 162 张；含 `judge_rules` 判定规则 |
| `data/equip_attrs.json` | 装备属性 26 件（`subtype` / `attack_range` / `distance_mod`） |
| `data/special_cards.json` | 专属牌 / 专属战法牌 / 特殊牌区 / 状态标记 / 概念 |
| `docs/元规则整理-完整版.md` | 规则侧 T0 母本（T0 指本项目内部约定的"权威母本文档"），`build_rule_corpus.py` 的唯一 source |

### 2.3 DWD / DWS（明细层）

| 文件 | 说明 |
|------|------|
| `data/rag_corpus/*.json` | 10 个语料任务产出的 12 个语料文件，脚本生成、可重建、进向量库 |
| `data/rag_corpus/*.md` | 每个语料 JSON 的同名 Markdown 双写（JSON 供检索，MD 供人工阅读与审计跳转） |
| `data/rag_corpus/核心规则摘要.md` | 仅 MD 无 JSON，**不入向量检索**，是"非 RAG 模式"的手维护规则速查兜底 |
| `data/raw_guides/jinxia/guides/` | 社区侧攻略 45 篇，已被 `build_guide_corpus.py` 加工成检索块 |
| `data/raw_guides/jinxia/combos/` | 社区侧配队素材（4 个 md + 2 个 csv），已被 `build_combo_corpus.py` 加工 |
| `data/hero_classification.json` | AI 全量修订产物（文件自述 `note`：基于 `heroes.json` 技能文本逐将核对；`source` 指向 `data/武将分类20260724.md`，标注"2026-08-15 AI 全量修订"），`hero_categories` 178 键、`categories` 16 类 |

> **入库 ≠ ODS**：`hero_classification.json` 虽在 `data/` 下，但它是 AI 修订成果，权威性不成立，故归 DWD。链路为 `heroes.json`(ODS) → 分类快照 MD → `hero_classification.json` → `武将分类语料.json`(检索块)。

### 2.4 mart（集市层，AI 生成产物）

| 文件 | 说明 |
|------|------|
| `data/guides.json` | 武将攻略，由 AI 经 RAG 检索 ODS+DWD 语料后生成 |
| `data/synergies.json` | 相性组合评分，同上 |

**只写不读**：这两个文件从不出现在任何 `build_*_corpus.py` 的 `sources` 中，也不进向量库。现有内容为旧版，将被 RAG 增强后的更高质量版本覆盖。

### 2.5 代码文件归属

```
src/rag/                          # 向量索引基础设施层
├── config.py                     # 路径 / 开关 / 检索参数 / KIND_MAX 类型配额
├── indexer.py                    # 语料规范化 + 向量化 + ChromaDB 写入
└── retriever.py                  # 混合检索（向量 + 关键词 RRF 融合）

src/business/rag/                 # 业务层（7 文件，本模块全部）
├── task_defs.py                  # 语料任务单一事实源（10 任务）
├── refinement_service.py         # 索引精化纯函数（扫描 / 建议 / 写回）
├── refinement_session.py         # 索引精化会话状态（三池 + 双基线）
├── suggest_controller.py         # LLM 建议 Qt 线程编排
├── rule_doc_service.py           # 元规则 T0 母本维护纯函数
├── audit_service.py              # 结构化审计（AuditIssue）
└── hero_brief.py                 # 武将概要视图（技能文本格式归位）

src/business/maintenance/         # 本模块占 3 文件
├── corpus_services.py            # 四个数据源写路径薄服务
├── classification_suggest.py     # 武将分类 LLM 建议
└── rule_doc_ops_service.py       # 元规则确认行校验与确认清单落盘

src/ui/maintenance/               # 维护工作台 UI（全部）
├── maintenance_workspace.py      # 工作台外壳（左栏导航 + 右工作区 + 折叠日志）
├── rag_maintenance_panel.py      # 业务逻辑：语料状态 / 审计横幅 / 一键重建
├── index_refinement_dialog.py    # 索引精化对话框（只渲染与确认）
├── rule_doc_panel.py             # 元规则母本四个子页签
├── card_points_panel.py          # 卡牌点数维护面板
└── equip_attrs_panel.py          # 装备属性维护面板

src/scripts/                      # 语料构建与维护脚本（见 4.5 参数表）
```

> **物理位置说明**：知识库数据源维护面板的文件**跨目录分布**。`hero_classification_panel.py`（武将分类）与 `special_cards_panel.py`（专属牌）位于 `src/ui/library/`，而非 `src/ui/maintenance/`；但两者的宿主都是 `src/ui/maintenance/maintenance_workspace.py` 装配的同一个工作台，由 `rag_maintenance_panel.py` 统一创建实例并接入左栏导航。

---

## 三、核心逻辑

本章按数据流向分八小节：先讲语料怎么生成、索引怎么建、检索怎么做，再讲索引精化与元规则维护两条人工工作流，最后讲数据源编辑与审计。

### 3.1 语料构建管线

语料由 `src/scripts/` 下的生成脚本产出，全部依赖 `rag_common.py` 提供的公共工具：`setup_stdout()` 统一 stdout UTF-8、`load_json()` 带 UTF-8-SIG（带 BOM 的 UTF-8）容错、`save_json()` 委托原子写、`get_script_logger()` 让诊断信息走 `logs/rag/<script>.log` 而非 stdout 进度通道。

**块 ID 稳定性是设计红线**，因为索引精化按 `block_id` 定位块：武将技能块用 `hero_{id}_skill_{技能名}`（用技能名而非数组序号，技能调序或增删不改变精化块定位）；元规则章节块 `rule_section_{章}` / `rule_section_{章}_{节}`、FAQ 块 `faq_%03d`（编号单调递增不回收，废弃条目划线保留编号）、术语块 `term_{名称}`；组合块 `combo_{A}_{B}`（武将名排序保证唯一；孟尝君+黄月英深解按曲拆 `_1..4`）；攻略块 `guide_{hero}_{i}`（按 `##` 章节序号）。

**跨任务联动**：`build_equip_attr.py` 除了产出装备属性语料，还会回写 `卡牌RAG语料.json`（把装备细分/攻击范围/距离修正注入装备牌块）。因此 `task_defs.py` 把 `卡牌RAG语料.json` 登记为"装备属性语料"任务的 source 之一，确保装备属性变更后卡牌语料也不会被判为最新。

**索引字段的两条来源**：

1. **规则抽取**（`build_rag_corpus.py`）—— `TIMING_PATTERNS` 三段正则抽时机、`FIXED_TRIGGER_PATTERNS` 固定句式 + `FIXED_TRIGGER_REGEXES` 抽触发条件、`TERMS`（按长度降序避免短词先命中）抽关键词、`RULE_MAP` + 牌名扫描抽关联引用。人工精化表 `TRIGGER_OVERRIDES`（已迁至 `src/data/hero_timeline.py` 供审计共用）命中时优先，不再走规则提取。
2. **人工精化 / 大模型精化**（`refinement_service.py`，见 3.5）。

**精化成果不被重建冲掉**：`rag_curated.merge_curated(blocks, old_json_path)` 在 build 脚本写文件前调用，把旧语料中同 `block_id` 的 `curated` 覆盖回新生成块的顶层索引字段并保留 `curated` 字段。

**增量调度**（`maintain_rag.py`）：`file_fingerprint()` 对文件源取 `(md5, size, mtime)`，对目录源（如 `raw_guides/jinxia/guides/`）聚合内部全部文件的相对路径 + 内容 md5（Windows 上不能直接 open 目录）；`task_changed()` 同时检查依赖源与脚本自身；状态落盘到项目根 `.rag_state.json`。关键防护：`update_state_fingerprints()` 让失败任务及其依赖路径一律保持旧指纹，否则失败任务的变更被"洗掉"后会永久跳过，坏语料一直驻留。

### 3.2 向量索引与嵌入

`src/rag/config.py` 定义路径与参数。`HF_HOME` / `SENTENCE_TRANSFORMERS_HOME`（缓存指向 `data/rag_models/models`）与 `HF_ENDPOINT=https://hf-mirror.com`（国内镜像）必须在 import 嵌入模型之前设置，顺序错了缓存位置就不生效。嵌入模型查找顺序：项目内 `data/rag_models/modelscope` → 环境变量 `RAG_MODEL_DIR` → 在线下载 `BAAI/bge-small-zh-v1.5`，由 `_find_local_model()` 用 `rglob("config.json")` 匹配 `bge-small-zh` 定位。

`indexer.build_index(rebuild=True)` 四阶段执行：

1. **加载语料** —— `load_all_blocks()` 遍历 `CORPUS_FILES`（12 个文件 × 对应 `_norm_*` 规范化函数）。缺文件只记 warning 跳过；规范化块数与源数据条数不一致直接抛 `ValueError`；块 ID 重复抛 `ValueError`（重复会让 Chroma 覆盖或写入失败）。统一注入版本元数据 `is_current` / `as_of`（默认 `CORPUS_BASE_DATE`）。
2. **加载模型** —— `SentenceTransformer(..., device='cpu')`。
3. **向量化** —— `model.encode(docs, batch_size=32, normalize_embeddings=True)`（归一化后余弦相似度等价于内积）。
4. **写入 ChromaDB** —— 集合名固定 `mjs_rag_v1`，`metadata={'hnsw:space': 'cosine', ...}`。HNSW（Hierarchical Navigable Small World，可导航小世界图）是 ChromaDB 的向量近似最近邻索引结构；`hnsw:space` 指定距离度量。批量 `BATCH=200`。

**重建与增量语义不同**：`rebuild=True` 先 `delete_collection` 再 `get_or_create_collection`；删除失败且集合仍存在时抛 `RuntimeError`（文件被占用时中止，而不是静默在残留旧向量上继续）。`rebuild=False` 则做集合同步：新增 `add` / 交集 `update` / 过期 `delete`，保留 collection 结构与 HNSW 索引。

### 3.3 混合检索

`Retriever.search(query, heroes=None, top_k=None)` 三路取块再融合：

1. **元数据硬过滤** —— `build_search_where(heroes)` 构造 Chroma `where` 条件，默认只召当前版本（`is_current='true'`），叠加武将名 `$in` 硬过滤保证人物召回。
2. **向量相似度** —— 查询先拼 `EMBEDDING_QUERY_INSTRUCTION`（"为这个句子生成表示以用于检索相关文章："），`normalize_embeddings=True` 编码；返回 `score = 1 - cosine distance`（余弦距离为 0 表示方向完全一致，故 1 减距离可转为相似度）。低于 `MIN_VECTOR_SCORE=0.25` 的块直接丢弃。
3. **关键词兜底** —— `_keyword_hits()`：静态 `KEYWORDS`（约 60 个规则高频状态/术语词）走惰性构建的倒排索引；查询里出现的武将/技能/牌名/术语名保持线性扫描。命中数乘以 `KEYWORD_BONUS=0.15` 得到绝对加分。

**RRF 融合**（Reciprocal Rank Fusion，倒数排名融合）：两路各自按分数降序排名，融合分 = `1/(RRF_K + rank)`，`RRF_K=60`。同一块两路都命中则两项相加，来源标记为 `vector+kw`。之所以用名次倒数而不是原始分数相加，是因为向量分数（0~1 连续）与关键词加分（0.15 的整数倍）量纲不同，直接相加会让某一侧主导结果。

**融合后的统一补齐**：关键词兜底走内存索引，绕过了向量侧的 `where` 过滤，因此融合后统一补回 `heroes` 硬过滤与 `is_current` 剔除，再对纯关键词块施加 `MAX_KEYWORD_ONLY=3` 的数量上限（RRF 单边分天然靠后，此上限仅防数量失控），最后 `_apply_kind_quota()` 按 `KIND_MAX` 类型配额截取 `top_k`。

### 3.4 RAG 注入 AI 生成

本模块的边界止于"召回块"。`Retriever` 对外提供两种召回口径：

- `search(query, heroes, top_k)` —— 按问题召回，`top_k` 默认 `TOP_K=12`
- `hero_blocks(hero)` —— 按武将名**全量**召回该武将的语料块（攻略/相性生成用，保证人物召回完整，不走相似度阈值）

提示词预算常量集中在 `config.py`：`MAX_PROMPT_CHARS=12000`（注入提示词的语料块总字符预算）、`RAG_PROMPT_CHARS=6000`、`RAG_SYNERGY_PROMPT_CHARS=6000`、`RAG_BROWSER_PROMPT_CHARS=3000`（浏览器自动化模式的收窄预算）。具体拼装逻辑（按 kind 分"官方硬依据 / 社区参考"两段、社区池 combo 优先 guide、官方未用块滚给社区）位于 `src/scraper/ai/rag_prompt.py`，归 `module_ai_batch.md`。

`KIND_MAX` 保证召回结果类型多样，不被单一语料占满：`hero` 6、`card` 2、`rule` 2、`faq` 2、`combo` 3、`guide` 2、`term` 1、`special` 1、`cardpt` 1、`equip` 1、`modify` 1、`classification` 1（共 12 类，与 `CORPUS_FILES` 一一对应）。其中 combo / guide 两条是社区素材配额（设计点 B）：组合块**不贴单值 `hero` 元数据**（避免 post-filter，即召回后再按条件筛选，丢掉配队中的一侧武将），但贴 `heroes: [hero_a, hero_b]` 列表供过滤；攻略块贴 `hero` 保证生成该武将时必召回。

### 3.5 索引精化三层架构

语料块的四个索引字段 `INDEX_FIELDS = ("timing", "trigger_condition", "keywords", "related")` 若规则抽取填不全，就在"索引精化"工作台补齐。逻辑下沉为三层，对话框只负责渲染与交互确认：

**① 纯函数服务层 `refinement_service.py`**

`scan_blocks(corpus_dir)` 一次扫描 `REFINABLE_FILES`（`卡牌RAG语料.json` / `武将RAG语料.json`）并**三分类**：

- `pending` 待精化 —— 无 `curated` 且任一索引字段为空
- `curated` 已精化 —— 有 `curated`（此时 `fields` 以 `curated` 内容为权威，并记录 `method` / `updated_at`）
- `normal` 普通块 —— 无 `curated` 且四字段全非空（构建规则已填满）

武将语料只取技能块（跳过 overview 块）。`suggest_one(block, generator)` 用 `REFINEMENT_SYSTEM_PROMPT` 提示 LLM 输出四字段 JSON，经 `extract_json()` 解析；`_to_update()` 截断上限为 timing/trigger_condition 各 8 条、keywords/related 各 12 条。`apply_curated()` / `clear_curated()` 原子读写语料文件。

**② 会话状态层 `RefinementSession`（纯 Python，无 Qt 依赖）**

持有**三池清单**、**双基线**与**行状态**：

- 三池：`_pending` / `_curated` / `_normal`，由构造时一次 `scan_blocks()` 初始化；`_total` 为初始待精化总数，作为进度条分母，不随保存/跳过变化
- 磁盘基线 `saved_baseline`：`block_id → {field: 文本}`，判定"是否真的改了"的依据
- LLM 基线 `llm_baseline`：本次会话的 LLM 建议内容，判定精化来源（`llm` 还是 `manual`）的依据
- 行状态 `row_states`：`pending` / `suggested` / `modified` / `refined` / `generated`，文案与颜色映射归 UI 层

`apply_updates(updates_by_file)` 按语料文件分组批量写回：**单文件失败不迁移其任何块**（内存与磁盘保持一致），其他文件正常保存，返回 `(成功块数, {文件名: 错误})`。

**③ Qt 线程编排层 `SuggestController`**

持有 worker 引用链与 generator 善后，dialog 销毁不连带析构运行中的线程。两个关键防护：模块级 `LIVE_WORKERS: set` 持有运行中的 `SuggestWorker`（防引用丢失被 GC 析构）；`cancel_and_shutdown()` 后仍在运行的 worker 转入 `_zombies` 列表持续持有，直到线程真正结束（QThread 在 `run()` 未结束时析构会导致整个进程崩溃）。

### 3.6 元规则 T0 文档维护

`docs/元规则整理-完整版.md` 是规则知识库的 T0 母本，原则是**只增不删、机器校验**。`rule_doc_service.py` 提供纯函数，命令执行由 UI 层 `rule_doc_panel.py` 用 QProcess 完成：

| 工作流 | 脚本 | 业务层职责 |
|--------|------|-----------|
| 文档校验 | `audit_rule_doc.py` | `parse_audit_output()` 解析 `[ERROR]/[WARN]/[INFO]` 行与"汇总："行；`audit_issue_counts()` 统计级别 |
| 数据段同步 | `sync_rule_stats.py` | `parse_sync_diff()` 读 `--json` 差异报告（段/行号/类型/旧值/新值）；`sync_json_path()` / `confirmed_diff_path()` 定位两个隐藏文件 |
| 变更提案 | `propose_rule_changes.py` / `apply_rule_proposal.py` | `list_proposals()` / `parse_proposal()` / `doc_target_line()` / `doc_section_context()` / `doc_line_at()` / `doc_context_around()`；`update_proposal_item()` 原位更新条目 |
| 疑难登记 | 无脚本（本地待办） | `load_pending()` / `add_pending()` / `pending_to_proposal()`（转 FAQ 新增提案）；`parse_doc_chapter7()` 只读解析文档第 7 章疑难表 |

`sync_rule_stats.py` 处理六个数据段 `SECTION_NAMES = ('0.1', '0.2', '3.1', '3.2', '3.5', '5.2')`，其中 `3.1/3.2` 是时机频次、`3.5` 是"每种牌限 1 次 / 首次类 / 累计阈值"（checkpoint 段仅报告不自动应用）。`rule_doc_service._doc_lines()` 带 mtime 缓存，文档未变化时不重复整文件读取。

### 3.7 数据源编辑与原子写回

五个可编辑数据源面板的写路径经 `corpus_services.py` 的四个薄服务中转，把 UI 与 `src/data` 仓储解耦；读写规则仍集中在仓储的原子写/回滚实现中，读查询经 `service.repository` 透传。

**两种保存语义**：卡牌点数、专属牌、装备属性是"**方法即落盘**"（每次 add/update/delete 立即写盘）；武将分类是"**内存修改 + 显式保存**"（面板 `mark_dirty` 后统一 `save()`），`ClassificationService` 单独只暴露 `save()` 与分类增删改方法。

底层统一委托 `src.data.json_repository.atomic_write_json()`：`mkstemp` 生成同目录唯一临时文件 → 写入后 `flush + fsync` → `os.replace` 原子替换；任一异常清理临时文件并重新抛出，原文件保持不变。写盘失败时仓储 `_save_or_rollback()` 恢复内存快照，避免"看似失败、实际已变"的脏状态。

`hero_brief.load_hero_briefs(root, fallback_names)` 是跨面板共享的武将概要视图，返回 `(names, positions, skills)` 三元组。技能文本格式 `名称：描述　结算：settlement` 是 RAG 语料域知识，归位业务层后 UI 不再自行拼接。

### 3.8 审计与工作流

`audit_service.audit_summary(root, pending_refinement=None)` 返回 `AuditIssue` 列表（frozen dataclass，含 `kind` / `message` / `severity` / `target_tab` / `target`），供 UI 渲染跳转按钮。`pending_refinement` 参数允许调用方传入已算好的待精化清单，避免同一轮刷新重复读语料文件。

**校验清单**（校验规则的花色/点数/张数/件数/细分类型/距离修正常量全部取自 `src/data` 各仓储，单一事实源）：

| kind | 校验内容 | 跳转目标 |
|------|---------|---------|
| `pending_refinement` | 索引字段待精化块数（排在第一位） | 直接打开索引精化对话框 |
| `unclassified_hero` | `heroes.json` 中未在 `hero_categories` 归类的武将 | 武将分类，`focus_unclassified()` |
| `orphan_category_key` | 分类表引用了 `heroes.json` 中不存在的武将（反向校验） | 武将分类 |
| `unknown_hero` | 专属牌 `hero` 字段引用未知武将 | 专属牌，`focus_item()` |
| `missing_settlement` | 专属牌/战法牌缺结算详情（"死士"为非实体牌标记，豁免） | 专属牌，`focus_item()` |
| `card_points_*` | 结构 / 总张数 162 / 异常花色 / 异常点数 | 卡牌点数 |
| `equip_attrs_*` | 结构 / 件数 26 / 细分类型 / 距离修正 | 装备属性 |
| `timeline_risk` | `TRIGGER_OVERRIDES` 失效风险、`heroes.json` 疑未同步 | 无跳转 |
| `*_unreadable` / `heroes_source_unavailable` | 数据源缺失或无法解析 | 对应面板 |

**审计双消费方**：`collect_*()` 系列函数由 UI 侧 `audit_summary()` 与脚本侧 `scripts/rag_audit.py` 共用，避免两侧各维护一份校验逻辑。`rag_audit.py` 额外做两项 UI 不做的事：技能描述中疑似牌名/道具名的启发式提取（`_SUFFIX` 结尾字 + `_BLACKLIST` 通用术语黑名单 + 已知名称区间覆盖，仅作人工确认提示），以及语料目录 `is_current='false'` 过时块统计。

**跳转实现**：`AuditIssue.target_tab` 仍存页签名（如"武将分类维护"），`removesuffix("维护")` 即左栏项 key，因此 `audit_service.py` 无需随布局重排改动。

**保存→重建闭环**：维护对象保存后发 `data_changed` → `refresh()` → 左栏对应项状态点即时变"待重建"并亮出 ↻，全程不离开当前视图。重建三档：左栏 ↻ 单项 `--only <语料>`、顶部"重建全部语料"`--force`、"重建语料+索引"`--force --build-index`（全模块唯一 `ROLE_PRIMARY`）。

---

## 四、接口说明

### 4.1 `src/rag/` 向量索引基础设施

**`config.py`（模块级常量）**

| 常量 | 值 | 说明 |
|------|-----|------|
| `CORPUS_DIR` | `data/rag_corpus` | 语料目录 |
| `CHROMA_DIR` | `data/rag_index/chroma` | ChromaDB 持久化目录 |
| `RAG_ENABLED` | `true` | RAG 总开关（环境变量 > `config.env` > 默认） |
| `TOP_K` | 12 | 默认召回块数 |
| `MAX_PROMPT_CHARS` | 12000 | 注入提示词的语料块总字符预算 |
| `KEYWORD_BONUS` | 0.15 | 关键词命中的绝对加分 |
| `RRF_K` | 60 | RRF 常数 |
| `MIN_VECTOR_SCORE` | 0.25 | 向量相似度下限（1 - 余弦距离） |
| `MAX_KEYWORD_ONLY` | 3 | 纯关键词命中块数量上限 |
| `KIND_MAX` | 见 3.4 | 12 类语料的类型配额 |

**`indexer.py`**

| 接口 | 签名 | 说明 |
|------|------|------|
| `load_all_blocks()` | `-> list[tuple[str, str, dict]]` | 读取全部 12 个语料 JSON，返回 `(block_id, text, metadata)`；缺文件 warning 跳过，块数不一致或 ID 重复抛异常 |
| `build_index()` | `(rebuild=True) -> (int, str)` | 全量重建或增量同步；返回 `(块数, 集合名)` |
| `CORPUS_FILES` | `list[tuple[str, Callable]]` | 12 个 `(文件名, 规范化函数)` 对，规范化函数契约：入参 JSON 数组，返回等长 `(block_id, text, meta)` 列表 |

**`retriever.py`**

| 接口 | 签名 | 说明 |
|------|------|------|
| `Retriever.search()` | `(query, heroes=None, top_k=None) -> list[dict]` | 混合检索主入口，返回块含 `block_id` / `text` / `metadata` / `score` / `source` / `rrf` |
| `Retriever.hero_blocks()` | `(hero) -> list[dict]` | 按武将名全量召回（不过阈值），`source='hero'` |
| `Retriever._set_blocks()` | `(blocks) -> None` | 注入内存语料并重建索引（生产走懒加载，测试用它注入内存语料） |
| `build_search_where()` | `(heroes=None) -> dict` | 构造 Chroma `where` 条件（`is_current` + 可选武将 `$in`） |
| `KEYWORDS` | `list[str]` | 61 个规则高频状态/术语词，关键词兜底用 |

属性 `collection` / `model` / `blocks` 均为惰性加载；`_id2meta`、`_id2text`、`_hero_index`、`_keyword_index` 是内部索引，随 `_set_blocks()` 重建。

### 4.2 `src/business/rag/`（7 文件）

**`task_defs.py`**

| 接口 | 说明 |
|------|------|
| `TASKS` | `list[dict]`，10 个任务。字段：`name` / `script` / `sources` / `outputs` / `expected` |

`expected` 三种语义：`int` = 精确匹配块数；`"snapshot"` = 以快照基线只增不删；`None` = 动态数量只报不校验。

| # | 任务名 | 脚本 | 期望块数 |
|---|--------|------|----------|
| 1 | 武将语料 | `build_rag_corpus.py` | 615 |
| 2 | 卡牌语料 | `build_card_corpus.py` | 49 |
| 3 | 点数花色语料 | `build_cardpts.py` | 49 |
| 4 | 装备属性语料 | `build_equip_attr.py` | 27 |
| 5 | 加强削弱语料 | `build_modify_corpus.py` | 49 |
| 6 | 元规则/术语/FAQ | `build_rule_corpus.py` | `"snapshot"` |
| 7 | 特殊机制语料 | `build_special_corpus.py` | 83 |
| 8 | 武将分类语料 | `build_classification_corpus.py` | `None` |
| 9 | 组合语料 | `build_combo_corpus.py` | `None` |
| 10 | 武将攻略语料 | `build_guide_corpus.py` | `None` |

> 任务 6 一个任务产出**三个** JSON（章节块 / 术语表 / FAQ 裁定块），故 10 个任务产出 12 个语料文件。

**`refinement_service.py`**

| 接口 | 说明 |
|------|------|
| `PendingBlock` / `RefinementUpdate` | 数据类：块视图（`corpus` / `block_id` / `name` / `kind` / `text` / `fields` / `missing` / `method` / `updated_at`）与精化结果（四字段 + `method` + `updated_at`） |
| `scan_blocks(corpus_dir)` | 一次扫描三分类，返回 `{"pending": [], "curated": [], "normal": []}` |
| `list_pending()` / `list_curated()` / `list_normal()` | 三分类之一的薄封装 |
| `suggest_one(block, generator)` | 单块 LLM 建议（公开接口），API/解析失败返回 `None` |
| `generate_suggestions(pending, generator)` | 逐块调用，返回 `{block_id: RefinementUpdate}`，单块失败跳过 |
| `apply_curated(corpus_dir, updates, fname)` | 写回精化结果：更新顶层索引字段并新增 `curated` 字段（原子保存），返回写入块数 |
| `clear_curated(corpus_dir, block_id, fname)` | 删除 `curated` 字段（取消精化）；块不存在抛 `ValueError`，本就没有 curated 返回 `False` |
| `build_generator(profile_name=None)` | 按 API 档案构造 `AIBatchGenerator`；供应商语义缺 Key 返回 `None` |
| `INDEX_FIELDS` / `REFINABLE_FILES` / `DEFAULT_CORPUS_DIR` | 常量：四字段元组 / 两个可精化语料文件名 / 语料目录 |

**`refinement_session.py` — `RefinementSession(corpus_dir)`**

| 接口 | 说明 |
|------|------|
| `pending` / `curated` / `normal` | 三池清单（只读属性） |
| `total` / `skipped_count` | 初始待精化总数 / 跳过条目数 |
| `saved_baseline` / `llm_baseline` / `row_states` | 双基线与行状态（只读属性，供对话框直读） |
| `blocks_for_scope(scope)` | `scope` 取 `pending` / `curated` / `all`，只过滤内存快照不重复读文件 |
| `is_pending(block_id)` | 判断块是否仍在待精化池 |
| `note_suggested(block, update)` | 记录批量建议：写 LLM 基线并置行状态 `suggested`（不回填编辑器） |
| `record_llm_baseline(block_id, baseline)` | 单块建议回填编辑器时同步基线（不改行状态） |
| `baseline_update(block_id)` | 把 LLM 建议基线还原为 `RefinementUpdate`；无建议返回 `None` |
| `collect_update(block_id, texts)` | 收集字段文本为 `RefinementUpdate`；与磁盘基线一致返回 `None` |
| `apply_updates(updates_by_file)` | 批量写回，返回 `(成功块数, {文件名: 错误})` |
| `sync_saved(block, update)` | 写盘成功后的内存同步（基线 / 行状态 / 池迁移） |
| `skip_block(block)` | 移出待精化清单并清理基线与行状态 |
| `clear_curated_block(block)` | 取消精化；写盘成功前不做任何内存变更，失败原样上抛 |

**`suggest_controller.py` — `SuggestController`**

| 接口 | 说明 |
|------|------|
| `start(blocks, generator, *, single=False)` | 启动批量/单块建议；`single=True` 时结果需回填编辑器 |
| `cancel_and_shutdown()` | 中止在途建议：worker 置取消、generator 先 `cancel` 再 `close`、`worker.wait(1000)` 短时等待，仍在运行则转僵尸列表持有 |
| `is_running` / `total` / `done` / `failed` / `current_worker` | 状态属性，供对话框渲染总览与按钮可用性 |
| 信号 `result_ready(block, update, is_single)` | 逐块结果转发，进度计数已先行更新 |
| 信号 `finished(is_single)` | worker 正常结束且 generator 已释放后发出（取消/关闭路径不发） |
| `SuggestWorker` | `QThread`，`parent=None` + `LIVE_WORKERS` 持有 + `finished→deleteLater` 自回收 |
| `LIVE_WORKERS` | 模块级 `set`，持有运行中 worker 防 GC 析构 |

**`rule_doc_service.py`**

| 分组 | 接口 |
|------|------|
| 常量 | `DEFAULT_DOC` / `PROPOSAL_DIR` / `PENDING_FILE` / `RAG_EVALS_DIR` / `VALID_PROPOSAL_STATUSES` |
| audit 解析 | `parse_audit_output(text)` / `audit_issue_counts(issues)` |
| 数据段差异 | `parse_sync_diff(path)` / `sync_json_path(root)` / `confirmed_diff_path(root)` |
| 提案读写 | `list_proposals(root)` / `parse_proposal(path)` / `update_proposal_item(root, proposal_path, item_id, status, edited_text=None)` |
| 文档定位 | `doc_target_line()` / `doc_section_context()` / `doc_line_at()` / `doc_context_around()` |
| FAQ 原位修订 | `build_faq_revise_row(line, text)`（脚本实合入与 UI 差异预览共用，仅替换裁定列 `cells[2]`） |
| 疑难登记 | `load_pending()` / `add_pending()` / `pending_to_proposal()` / `parse_doc_chapter7()` |

**`audit_service.py`**

| 接口 | 说明 |
|------|------|
| `AuditIssue` | frozen dataclass：`kind` / `message` / `severity` / `target_tab` / `target` |
| `audit_summary(root, pending_refinement=None)` | 汇总审计条目，空列表表示无问题 |
| `format_audit_issues(issues)` | 结构化条目 → 纯文本列表（兼容旧消费方/测试） |
| `collect_card_points(payload)` | 卡牌点数校验（`structure` / `total` / `bad_suit` / `bad_point`） |
| `collect_equip_attrs(equips)` | 装备属性校验（`structure` / `count` / `bad_subtype` / `bad_distance`） |
| `collect_missing_settlements(specials)` | 专属牌/战法牌缺结算详情（死士豁免） |
| `collect_unclassified(hero_names, classification)` | 未归类武将名（升序） |
| `collect_orphan_category_keys(hero_names, classification)` | 分类表引用未知武将（反向校验，升序） |
| `collect_unknown_heroes(specials, hero_names)` | 专属牌引用未知武将（泛指/括号注释跳过，升序） |
| `collect_timeline_risk_messages(root)` | 时间轴风险摘要（UI 横幅用） |
| `GENERIC_HERO_NAMES` | 泛指/占位武将名 `{"通用", "—", "众多武将"}`，校验与 UI 共用 |

**`hero_brief.py`**

| 接口 | 说明 |
|------|------|
| `load_hero_briefs(root, fallback_names=None)` | 返回 `(names: set[str], positions: dict[str,str], skills: dict[str,str])`；`heroes.json` 缺失时用传入回退集合，技能文本按行拼接 |

### 4.3 `src/business/maintenance/`（RAG 三文件）

| 文件 | 接口 | 说明 |
|------|------|------|
| `corpus_services.py` | `CardPointsService` | `add_card` / `replace_card` / `delete_card` / `add_rule` / `update_rule` / `delete_rule` + `repository` 透传 |
| | `SpecialCardsService` | `add_item` / `update_item` / `delete_item` + `repository` |
| | `ClassificationService` | `save` / `add_category` / `update_category` / `delete_category` / `set_counter_chain` / `set_hero_categories` + `repository` |
| | `ComboService` | `save_manual_combo` / `delete_combo` + `repository`（归 `module_peak_combos.md`） |
| `classification_suggest.py` | `suggest_hero_categories(hero, skills_text, position, categories, generator)` | LLM 从分类清单中选机制分类（可多选）；返回 `None`=失败，`list`=已过滤（只含清单内、去重保序，可能空） |
| `rule_doc_ops_service.py` | `validate_confirmed_row(diff, new)` | 校验确认行：非空、完整表格行（以竖线开头结尾）、列数与原文一致；返回错误消息，`None` 表示通过 |
| | `save_confirmed_diffs(root, rows)` | 确认清单原子写盘（`sync_rule_stats.py --apply-json` 的输入） |

### 4.4 `src/ui/maintenance/`

| 文件 | 关键接口 |
|------|---------|
| `maintenance_workspace.py` | `MaintenanceSourceNav`（`WIDTH=230`；信号 `source_selected` / `rebuild_requested` / `meta_requested`；`add_group` / `add_source` / `select` / `set_task_states`）；`MaintenanceWorkspace`（`LOG_COLLAPSED_HEIGHT=32` / `LOG_EXPANDED_HEIGHT=180`；`add_source` / `select_source` / `set_interactive` / `expand_log` / `collapse_log` / `on_log_output` / `reset_unread` / `set_log_meta`） |
| `rag_maintenance_panel.py` | `RagMaintenancePanel`（信号 `data_changed`；`refresh()` / `reload_data()` / `_run(args)` / `_jump_to_issue(issue)` / `_show_corpus_meta(key)`）；`task_states(root)` 计算 `最新`/`待重建`/`缺源`；`_output_count(path)` 带 `(mtime, size)` 缓存；`EDITABLE_SOURCE_ITEMS` 5 项 / `READONLY_CORPUS_ITEMS` 5 项 / `_MAX_AUDIT_ROWS=3` |
| `index_refinement_dialog.py` | `IndexRefinementDialog(corpus_dir, parent)`，`resize(1160, 720)`。顶部三档范围 `pending`/`curated`/`all`；行状态 `pending`/`suggested`/`modified`/`refined`/`generated`；字段状态 `empty`/`llm`/`manual`/`saved` |
| `rule_doc_panel.py` | `RuleDocPanel(root)`，四个子页签（文档状态 / 数据段差异 / 提案工作台 / 疑难登记）；信号 `data_changed` / `script_started` / `script_output(bytes)` / `script_finished(int)` |
| `card_points_panel.py` | `CardPointsPanel(repository, root)`，信号 `data_changed`；牌行 + `judge_rules` 判定规则增删改；「从 xlsx 导入」经 `ScriptRunner` 异步执行 |
| `equip_attrs_panel.py` | `EquipAttrsPanel(repository)`，信号 `data_changed`；列 `("名称","细分类型","攻击范围","距离修正","备注")`，名称/备注只读 |

> 另外两个维护对象面板位于 `src/ui/library/`：`hero_classification_panel.py`（`HeroClassificationPanel`，含 `focus_unclassified()`）、`special_cards_panel.py`（`SpecialCardsPanel`，含 `focus_item(category, name)`）。

### 4.5 CLI 脚本参数表

除 `maintain_rag.py` 外的脚本均以 `python -m src.scripts.<模块名>` 运行。无参数脚本（`build_card_corpus.py` / `build_cardpts.py` / `build_equip_attr.py` / `build_modify_corpus.py` / `build_special_corpus.py` / `build_classification_corpus.py` / `rag_audit.py`）直接运行即执行。

| 脚本 | 作用 | 参数 | 影响的层 |
|------|------|------|---------|
| `maintain_rag.py` | 语料维护调度主脚本，按依赖顺序重跑 build 脚本 | `--force` 强制重跑全部；`--check` 只检测不执行；`--only 关键词` 只跑名称含关键词的任务；`--keep-going` 单个失败后继续；`--build-index` 语料更新后重建向量索引；`--strict-audit` 审计未覆盖项视为失败 | DWD（+ 向量索引） |
| `src.rag.indexer` | 构建向量索引 | `--no-rebuild` 增量同步（默认全量重建） | 向量索引 |
| `src.rag.retriever` | 检索测试（不调用 LLM） | `--query`（必填）/ `--hero` 可重复 / `--top-k` | 只读 |
| `audit_rule_doc.py` | 元规则 T0 文档机器校验 | `--strict` 有 ERROR/WARN 退出码 1；`--update-snapshot` 校验后刷新基线快照；`--doc`；`--snapshot` | ODS（只读，快照为审计基准） |
| `sync_rule_stats.py` | 元规则数据段同步（六个数据段） | `--only 0.1` 等只处理指定段；`--apply` 应用差异（默认仅报告）；`--apply-candidates` 同时应用候选段；`--apply-json` 应用确认清单（退出码 0=成功/1=预检失败/2=前置失败）；`--doc`；`--json` 差异报告输出路径 | ODS（可写） |
| `propose_rule_changes.py` | 变更提案起草器 | `--changes-json` 变更清单 JSON；`--no-llm` 不调用 LLM 生成占位提案；`--out-dir`；`--doc` | ODS（只读）+ 提案档案 |
| `apply_rule_proposal.py` | 提案合入器 | `--proposal`（必填）；`--doc`；`--skip-maintain`；`--skip-audit` | ODS（可写）+ DWD |
| `eval_rule_faqs.py` | FAQ 裁定回归评估 | `--generate` 生成/重建评估集后退出；`--dataset`（默认 `data/rag_evals/rule_faq_eval.json`）；`--top-k`；`--limit` | 只读（评测非检索） |
| `eval_guide_quality.py` | 攻略/相性生成质量评估 | `--pick-sample` 采样 20 武将 + 10 对；`--stats`；`--guides`；`--synergies`（可多个）；`--attempts` | 只读 |
| `diff_source_data.py` | 数据源变更清单生成 | `--old`（默认 `data/backups`）；`--data` 逗号分隔；`--out` 输出 markdown 路径 | 只读 |
| `run_synergy_drift.py` | 10 对 × 3 次相性漂移采样 | `--out-prefix`；`--rounds`（默认 3）；`--pairs`；`--heroes` | 只读（产物可删） |
| `build_*.py`（8 个） | 语料生成 | 无参数 | DWD |

---

## 五、关键代码片段

### 5.1 混合检索的 RRF 融合（`src/rag/retriever.py`）

```python
def search(self, query, heroes=None, top_k=None):
    """混合检索：向量 + 关键词经 RRF 排名融合，再按类型配额取 top_k。
    heroes: 指定武将名列表（元数据硬过滤，保证召回）。"""
    top_k = top_k or config.TOP_K
    where = build_search_where(heroes)
    vec = [v for v in self._vector_search(query, where=where, n=max(top_k * 2, 30))
           if v['score'] >= config.MIN_VECTOR_SCORE]
    kw = self._keyword_hits(query)

    # RRF：向量与关键词各自按分数降序排名，融合分 = 1/(RRF_K + rank)
    merged = {}
    for r, item in enumerate(sorted(vec, key=lambda x: x['score'], reverse=True), start=1):
        merged[item['block_id']] = dict(item, rrf=1.0 / (config.RRF_K + r), source='vector')
    for r, (bid, bonus) in enumerate(sorted(kw.items(), key=lambda x: x[1], reverse=True), start=1):
        item = merged.get(bid)
        if item is None:
            item = {'block_id': bid, 'text': self._text_of(bid),
                    'metadata': self._meta_of(bid), 'score': bonus, 'source': 'keyword'}
            merged[bid] = item
        item['rrf'] = item.get('rrf', 0.0) + 1.0 / (config.RRF_K + r)
        if item['source'] == 'vector':
            item['source'] = 'vector+kw'

    # 关键词兜底走内存索引，绕过了向量侧 where 过滤；融合后统一补齐
    # heroes 硬过滤（与 build_search_where 的 $in 语义一致）与 is_current 剔除
    hero_filter = set(heroes) if heroes else None
    merged = {bid: item for bid, item in merged.items()
              if item.get('metadata', {}).get('is_current', 'true') != 'false'
              and (hero_filter is None
                   or item.get('metadata', {}).get('hero') in hero_filter)}
    ...
    ranked = sorted(merged.values(), key=lambda x: x['rrf'], reverse=True)
    return self._apply_kind_quota(ranked, top_k)
```

**解读**：三个容易漏掉的细节——① 向量侧 `n=max(top_k*2, 30)` 取两倍召回量，给关键词融合留出余量，否则阈值过滤后块数不足；② 融合后必须**重新补齐**硬过滤，因为 `_keyword_hits()` 走的是内存块索引而非 Chroma `where`，向量侧的过滤对它无效，否则会出现"指定武将 A 却召回武将 B 的块"；③ 两个通道量纲不同（相似度 0~1 vs 加分 0.15 整数倍），用名次倒数而非原始分求和才能避免单侧主导。

### 5.2 精化写回入口（`src/business/rag/refinement_session.py`）

```python
def collect_update(self, block_id: str, texts: dict[str, str]) -> RefinementUpdate | None:
    """把字段文本收集为 RefinementUpdate；与磁盘基线一致（无改动）返回 None。

    method 判定沿用现状：与本次 LLM 建议完全一致 → llm，否则 manual。
    """
    saved = self._saved_baseline.get(block_id, {})
    llm = self._llm_baseline.get(block_id)
    values: dict[str, list[str]] = {}
    changed = False
    for field in INDEX_FIELDS:
        text = texts[field]
        values[field] = [line.strip() for line in text.splitlines() if line.strip()]
        if text != saved.get(field, ""):
            changed = True
    if not changed:
        return None
    if llm is not None:
        modified = any(texts[f] != llm.get(f, "") for f in INDEX_FIELDS)
        method = "manual" if modified else "llm"
    else:
        method = "manual"
    return RefinementUpdate(
        timing=values["timing"],
        trigger_condition=values["trigger_condition"],
        keywords=values["keywords"],
        related=values["related"],
        method=method,
    )
```

**解读**：这里用**双基线**解决两个不同的判定问题。磁盘基线回答"用户到底改没改"——完全一致就返回 `None`，让 UI 跳过保存，避免把已有内容反复写盘；LLM 基线回答"这份内容是机器给的还是人改的"——与 LLM 建议逐字段完全一致才标 `method="llm"`，否则 `"manual"`，反映人工干预程度。这一区分直接影响块在界面上的标签与后续审计口径。

配套的批量写回 `apply_updates()`：

```python
def apply_updates(
    self, updates_by_file: dict[str, dict[str, RefinementUpdate]],
) -> tuple[int, dict[str, str]]:
    """按语料文件分组批量写回并同步内存。

    Returns:
        (成功保存块数, {文件名: 错误信息})——出错的文件不迁移其任何块。
    """
    saved = 0
    errors: dict[str, str] = {}
    for fname, updates in updates_by_file.items():
        try:
            apply_curated(self._corpus_dir, updates, fname)
        except (OSError, ValueError) as error:
            logger.error("保存精化失败 %s: %s", fname, error)
            errors[fname] = str(error)
            continue  # 出错文件不迁移任何块
        for block_id, update in updates.items():
            block = next(b for b in (*self._pending, *self._curated, *self._normal)
                         if b.block_id == block_id)
            self.sync_saved(block, update)
            saved += 1
    return saved, errors
```

**解读**：`continue` 而非 `break` 是关键——单文件失败不阻断其他文件，但也不迁移该文件的任何块，保证内存与磁盘状态一致（不会出现"界面显示已精化、磁盘其实没写"的假成功）。

### 5.3 语料任务注册机制（`src/business/rag/task_defs.py`）

```python
TASKS: list[dict] = [
    {
        "name": "武将语料",
        "script": "build_rag_corpus.py",
        "sources": ["data/heroes.json", "data/cards.json", "data/mjs_adjustments.json"],
        "outputs": ["武将RAG语料.json"],
        "expected": 615,
    },
    ...
    {
        "name": "组合语料",
        "script": "build_combo_corpus.py",
        "sources": [
            "data/raw_guides/jinxia/combos/bilibili_videos_weijiang.csv",
            "data/raw_guides/jinxia/combos/强力组合.md",
            "data/raw_guides/jinxia/combos/巴清搭配.md",
            "data/raw_guides/jinxia/combos/平阳公主强势组合盘点.md",
            "data/raw_guides/jinxia/combos/孟尝君 + 黄月英.md",
            "data/heroes.json",
        ],
        "outputs": ["组合RAG语料.json"],
        "expected": None,
    },
]
```

**解读**：这是新增或修改语料任务的**唯一入口**。同一个 `TASKS` 被三个消费方读取：`rag_maintenance_panel.py` 的 `TASK_DEFS`（`[(task["name"], task["sources"], task["outputs"]) for task in _RAG_TASKS]`）用于左栏状态点与"待重建"判定；`maintain_rag.py` 用于增量调度、依赖顺序与块数校验；`verify_outputs()` 按 `expected` 三种语义校验产出。新增任务只需在此追加一条 dict，无需改 UI 或调度代码。

> **注意**：`sources` 不只是输入依赖，也参与 `task_changed()` 的变更检测。`装备属性语料` 任务的 sources 里登记了 `data/rag_corpus/卡牌RAG语料.json`，因为 `build_equip_attr.py` 会回写该文件——若不登记，装备属性变更后卡牌语料会被误判为"最新"。

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.json_repository` | `atomic_write_json()`：语料 JSON、curated 写回、提案/确认清单全部经此原子写盘 |
| 依赖 | `src.data.card_points_repository` | `EXPECTED_TOTAL_CARDS=162` / `VALID_SUITS`（♥♣♠♦太极）/ `VALID_POINTS`（1~8）——卡牌点数校验常量单一事实源 |
| 依赖 | `src.data.equip_attrs_repository` | `EXPECTED_EQUIP_COUNT=26` / `VALID_SUBTYPES`（武器/防具/坐骑）/ `VALID_DISTANCE_MODS` |
| 依赖 | `src.data.hero_classification_repository` | 武将分类数据源仓储（UI 面板持有，审计读 JSON） |
| 依赖 | `src.data.special_cards_repository` | 专属牌数据源仓储 |
| 依赖 | `src.data.combo_manager` | `ComboService` 的手工配队写路径（归 `module_peak_combos.md`） |
| 依赖 | `src.data.hero_timeline` | `CORPUS_BASE_DATE` / `TRIGGER_OVERRIDES` / `stale_overrides` / `load_timeline` / `hero_last_change` / `stamp_hero_block` / `stamp_guide_block`——语料版本戳与人工精化触发条件表 |
| 依赖 | `src.scraper.ai.api_generator` | `AIBatchGenerator.complete()`：精化建议与武将分类建议的 LLM 调用 |
| 依赖 | `src.scraper.ai.json_extract` | `extract_json()` 解析 LLM 输出 |
| 依赖 | `src.config.env` | `PROJECT_ROOT` / `parse_env_file()` / `resolve_api_config()` / `PROVIDER_PRESETS` |
| 依赖 | `src.ui.shared.widgets` | `ScriptRunner`（QProcess 异步执行脚本）/ `PageActionBar` / `StatusBadge` / `DialogFooter` |
| 依赖 | `src.ui.library.hero_classification_panel` / `special_cards_panel` | 两个维护对象面板（物理位置在 library 下） |
| 依赖 | `chromadb` / `sentence-transformers` / `tqdm` | 向量库客户端、本地嵌入模型、进度条 |
| 被调用方 | `src.scraper.ai.rag_prompt` | 经 `Retriever.search()` / `hero_blocks()` 取召回块并拼装提示词（提示词拼装归 `module_ai_batch.md`） |
| 被调用方 | `src.ui.app.main_window` | 主导航第 4 页挂载 `RagMaintenancePanel` |
| 被调用方 | `src.business.rag.refinement_service` ← `audit_service` | `list_pending()` 供 `audit_summary()` 计算待精化块数 |

**交叉引用（不归本模块，仅在此声明边界）**：

- `src/data/` 的 `hero_classification_repository.py` / `equip_attrs_repository.py` / `special_cards_repository.py` / `card_points_repository.py` / `recommendation_index_repository.py` / `json_repository.py` → 归 `module_data.md`
- `src/scraper/ai/rag_prompt.py` / `prompt_utils.py` → 归 `module_ai_batch.md`
- `src/business/maintenance/combo_import_service.py` → 归 `module_peak_combos.md`；`data_management_service.py` → 归 `module_business.md`
- `src/scripts/` 的 `import_combos.py`（peak_combos）、`import_hero_adjustments.py` / `migrate_excel_to_json.py`（data）、`build_character_feature_cache.py`（capture_ocr）、`capture_ui_baselines.py`（ui）

---

## 七、待确认信息清单

1. **【假设】武将语料期望块数漂移**：`task_defs.py` 写 `expected=615`，磁盘 `data/rag_corpus/武将RAG语料.json` 实测 **622** 块。按当前 `heroes.json`（180 武将 / 442 技能）推算 180 总览块 + 442 技能块 = 622，与磁盘一致；615 疑为 `heroes.json` 于近期更新后 `task_defs.expected` 未同步。改 ODS 数据后重建语料须同步更新 `expected`，否则 `maintain_rag.py --strict-audit` 会因块数漂移报警。**待确认**：615 是否为待修的正确值，或应改为 622。
2. **【假设】模块间文档的过期数字**：`module_data.md` 末尾记"组合 RAG 语料 437 块"，磁盘实测 **509** 块（`expected=None` 动态值，不报错但文档已过期）；`module_ui.md` 第七节与 `equip_attrs_panel.py` docstring 记"26 件装备"，与仓储常量 `EXPECTED_EQUIP_COUNT=26` 一致，但语料块数为 **27**——差额 1 块是 `build_equip_attr.py` 额外追加的 `equipattr_规则_距离计算` 规则块（已在 3.1 核实）。
3. **【假设】`rag_curated.py` 与 `refinement_service.py` 的字段集不一致**：`rag_curated.INDEX_FIELDS` 含 5 个字段 `("timing", "trigger_condition", "keywords", "related", "target")`，而 `refinement_service.INDEX_FIELDS` 只有 4 个（已去掉 `target`）。重建时 `merge_curated()` 仍会把旧 `curated` 中的 `target` 覆盖回块顶层。若 `target` 已从精化流程退役，`rag_curated.py` 的字段集是否也应同步收敛为 4 个，待确认。
4. **【假设】`build_cardpts.py` 块数与牌行数关系**：`card_points.json` 有 72 行牌面明细（`count` 合计 162），脚本按**牌名去重聚合**产出 49 块（与 `expected=49` 一致）。聚合规则已核实，但 72 行的 `suit`/`point` 组合维度在聚合中是否仍有信息丢失，属设计取舍，未进一步核对。
5. **【假设】`data/rag_corpus/` 实际文件清单**：磁盘有 12 个 `.json` + 12 个同名 `.md` + 1 个仅 MD 的 `核心规则摘要.md`。`indexer.CORPUS_FILES` 只登记 12 个 JSON，与磁盘一致；`核心规则摘要.md` 确实不入向量检索（代码注释已证），但它与 `元规则RAG语料-章节块.md` 的内容是否有重叠未逐条比对。
6. **【假设】时间轴数据源**：`data/mjs_adjustments.json` 磁盘存在，被登记为"武将语料"任务的 source，`build_guide_corpus.py` 也调用 `load_timeline()` / `stamp_guide_block()`。但其由 `import_hero_adjustments.py` 导入（归 data 模块），权威性归属（ODS 还是中间产物）未在本模块文档内界定。
7. **【假设】审计未实际运行**：`collect_orphan_category_keys()` 以 `hero_categories` 键（实测 178）减 `heroes.json` 武将名（实测 180）做反向校验。本轮未运行 `audit_summary()` 输出，"是否存在孤儿键及其数量"以实际运行结果为准，本文不给出具体数量。
8. **【假设】评测集规模与集合条目数**：`data/rag_evals/rule_faq_eval.json` 磁盘存在（`eval_rule_faqs.py` 默认数据集），题目条数未统计（历史记忆记 79 题，与 `FAQ裁定块.json` 的 79 块数值巧合，是否同源未验证）。同理，12 个语料 JSON 磁盘合计 2088 块（79+38+49+49+49+48+622+178+357+83+509+27），但 ChromaDB 集合 `mjs_rag_v1` 的实际条目数未打开核对，二者理论上应相等。
