# 调用链路：RAG 知识库模块

> 对应源码：`src/rag/`、`src/business/rag/`、`src/business/maintenance/` 的 RAG 三文件、`src/ui/maintenance/` 全部、`src/scripts/` 的语料与维护脚本。
> 代码基线：`77e9407`（2026-09-04）。
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。
> 虚线 `───` 表示跨越进程边界（QProcess / subprocess 子进程）。
> 与 AI 批量生成、巅峰赛识别、实战配队相关的调用链路见 [call_graph_ai_batch.md](./call_graph_ai_batch.md)、[call_graph_peak_combos.md](./call_graph_peak_combos.md)；业务服务层与界面层总览见 [call_graph_business.md](./call_graph_business.md)、[call_graph_ui.md](./call_graph_ui.md)。

---

## 通俗概述

这套代码解决的问题是：**让 AI 在生成武将攻略和相性评分时，能查到正确的游戏规则，而不是凭空编造。**

整个流程可以拆成四段。第一段是"喂料"：游戏规则散落在武将表、卡牌表、规则文档等十几份文件里，几十个 `build_*_corpus.py` 脚本把它们各自整理成一份份"知识块"，存在 `data/rag_corpus/` 文件夹下。第二段是"建目录"：程序把这些文字块转成一串串数字（向量），连同原文一起存进一个本地数据库，方便以后按相似度找。第三段是"查资料"：AI 要生成某武将攻略前，先去这个目录里按关键词和相似度各搜一轮，把两份结果融合排序，挑出最相关的一批规则塞进给 AI 的提示词里。第四段是"维护"：程序提供一整套桌面界面，让人工检查规则有没有漏、补全索引字段、修订规则母本文档，改完一键重建语料。

本模块的边界：**语料怎么造、索引怎么建、检索怎么融合、AI 生成时怎么注入、语料怎么人工维护**，都在这里。至于给 AI 发消息、解析 AI 返回的 JSON、生成攻略和相性本身，属于 [call_graph_ai_batch.md](./call_graph_ai_batch.md)；武将表、卡牌表等数据文件的读写仓储，属于 module_data；巅峰赛配队的组合导入，属于 module_peak_combos。

## 术语表（首次出现解释）

| 术语 | 英文全称 | 一句大白话 |
|------|----------|------------|
| RAG | Retrieval-Augmented Generation（检索增强生成） | 先查资料再让 AI 回答，避免 AI 凭记忆编规则 |
| ChromaDB | Chroma DB（开源向量数据库） | 一个能存"文字→数字"并把相似数字找出来的本地数据库 |
| bge | BAAI General Embedding（百度百度的通用中文嵌入模型，本项目用 bge-small-zh-v1.5） | 把一段中文转成一串能算相似度的数字的小模型 |
| 向量 / 嵌入 | Embedding | 文字的"数学指纹"，含义越接近的两段文字指纹越像 |
| RRF | Reciprocal Rank Fusion（倒数排名融合） | 两份搜索结果列表，按各自排名倒数相加来综合打分的方法 |
| ODS / DWD / mart | 数据仓库分层（原始层 / 明细层 / 集市层） | 原始数据、加工后数据、面向应用的成品数据三层 |
| T0 母本 | Tier-0 master document | 全项目唯一的规则权威文档，其他语料都以它为准 |
| curated | 无固定英文全称，取自精化标记字段名 | 已经被人或 AI 精修过的索引字段，重建语料时不会被覆盖 |
| 语料块 | Corpus block | 一条知识的最小单位，有唯一 `block_id` 和一段可检索的文本 |
| Pydantic | Pydantic（Python 数据校验库） | 用 `model_validate()` 检查字段是否合法的框架 |

## 一、RAG 底层三件套（src/rag/）

本模块只含三个文件，职责严格分工：`config.py` 集中路径与检索参数、`indexer.py` 把语料 JSON 变成向量写进 ChromaDB、`retriever.py` 做混合检索。三者都不依赖 Qt，GUI 进程和脚本进程共用。

### 1.1 配置层 config.py

```
config.py 模块导入时（无函数，纯常量初始化）
  -> parse_env_file()                                        [src.config.env 解析 config.env]
  -> os.environ.setdefault("HF_HOME" / "SENTENCE_TRANSFORMERS_HOME" / "HF_ENDPOINT")
  -> _to_int("RAG_TOP_K", 12) 等 4 个数值参数解析
  -> _find_local_model()                                     [本地 bge 模型探测]
     -> MODELSCOPE_DIR.rglob("config.json")                  [优先项目内 modelscope 缓存]
     -> [未命中] 回退 RAG_MODEL_DIR
     -> [均未命中] EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"（在线下载）
```

关键常量：`CORPUS_DIR`（语料目录）、`RAG_INDEX_DIR` / `CHROMA_DIR`（向量库目录）、`EMBEDDING_MODEL`（本地 bge 路径）、`EMBEDDING_QUERY_INSTRUCTION`（查询前置指令）、`TOP_K`（默认 12）、`RRF_K`（默认 60）、`MIN_VECTOR_SCORE`（默认 0.25）、`MAX_KEYWORD_ONLY`（默认 3）、`KEYWORD_BONUS`（默认 0.15）、`KIND_MAX`（12 类语料的每条结果配额上限）、`MAX_PROMPT_CHARS`（默认 12000）。

| 常量 / 函数 | 所在文件 | 作用 | 调用方 |
|-------------|----------|------|--------|
| `EMBEDDING_MODEL` | `rag/config.py` | 向量模型标识（本地路径或 HF 名） | `indexer.build_index()`, `Retriever.model` |
| `KIND_MAX` | `rag/config.py` | 12 类语料的结果配额：hero 6 / card 2 / rule 2 / faq 2 / term 1 / special 1 / cardpt 1 / equip 1 / modify 1 / classification 1 / combo 3 / guide 2 | `Retriever._apply_kind_quota()` |
| `RRF_K` | `rag/config.py` | RRF 融合常数 | `Retriever.search()` |
| `_find_local_model()` | `rag/config.py` | 探测本地 bge 模型目录 | 模块导入 |
| `_to_int(key, default)` | `rag/config.py` | 环境变量数值解析兜底 | 模块导入 |

### 1.2 索引器 indexer.py

```
build_index(rebuild=True)                                        [CLI 入口：python -m src.rag.indexer]
  -> load_all_blocks()                                          [1/4 加载语料]
     -> [遍历 CORPUS_FILES 12 项 (文件名, _norm_*)]
        -> json.loads(CORPUS_DIR / fname)
        -> _norm_hero / _norm_card / _norm_rule / _norm_term /
           _norm_faq / _norm_special / _norm_cardpt / _norm_equip /
           _norm_modify / _norm_classification / _norm_combo / _norm_guide
           [各把源 JSON 块转成 (block_id, text, metadata)，注入 kind 标签]
        -> [len(blocks) != len(data)] raise ValueError           [规范化块数须与源 1:1]
        -> meta.setdefault('is_current' / 'as_of')              [版本元数据回填，默认只召当前版本]
  -> [block_id 唯一性校验] 重复 raise ValueError
  -> SentenceTransformer(EMBEDDING_MODEL, device='cpu')          [2/4 加载向量模型]
  -> model.encode(docs, batch_size=32, normalize_embeddings=True) [3/4 向量化]
  -> chromadb.PersistentClient(path=CHROMA_DIR)                  [4/4 写入]
  -> client.get_or_create_collection('mjs_rag_v1', metadata={'hnsw:space': 'cosine'})
  -> [rebuild=True] client.delete_collection('mjs_rag_v1')
       -> [删除失败且集合仍存在] raise RuntimeError              [防止在残留旧向量上重建]
       -> coll.delete(where={}) -> coll.add(ids, documents, metadatas, embeddings)  [BATCH=200]
  -> [rebuild=False] existing = coll.get(include=[])['ids']
       -> coll.delete(ids=过期块) -> coll.add(新增) -> coll.update(交集)
  -> return (coll.count(), coll_name)
```

`CORPUS_FILES` 共 12 项，与 10 个语料构建任务对应（"元规则/术语/FAQ"一个任务产出 3 个文件）。缺失语料文件走 `logger.warning` 跳过而非报错——因为 GUI 检索路径经 `Retriever.blocks` 也会调用 `load_all_blocks()`。

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `build_index(rebuild)` | `scripts/maintain_rag.py`（子进程）、`rag/indexer.py` `__main__` | 全量重建或增量同步 ChromaDB 集合 | 维护面板「重建语料+索引」 |
| `load_all_blocks()` | `indexer.py` | 读全部语料 JSON 并规范化，返回 `(block_id, text, metadata)` | `build_index()`, `Retriever.blocks` |
| `_norm_hero(blocks)` | `indexer.py` | 武将总览块 + 技能块文本规范化 | `load_all_blocks()` |
| `_norm_card / _norm_rule / _norm_term / _norm_faq` | `indexer.py` | 其余单类语料规范化 | `load_all_blocks()` |
| `_norm_special / _norm_cardpt / _norm_equip` | `indexer.py` | 特殊机制/点数花色/装备属性规范化 | `load_all_blocks()` |
| `_norm_modify / _norm_classification / _norm_combo / _norm_guide` | `indexer.py` | 加强削弱/分类/组合/攻略规范化 | `load_all_blocks()` |

### 1.3 检索器 retriever.py

`Retriever` 是懒加载单例（`rag_prompt._get_retriever()` 进程内复用），三个 property 各管一个重量级资源：`collection`（ChromaDB 集合）、`model`（bge 模型）、`blocks`（内存块索引，供关键词兜底与 `hero_blocks`）。

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `build_search_where(heroes)` | `retriever.py` | 构造 Chroma `where`：默认 `is_current=true`，可叠加 `hero $in` 硬过滤 | `Retriever.search()` |
| `Retriever._vector_search(query, where, n)` | `retriever.py` | bge 编码查询 + `collection.query()` 向量检索，`score = 1 - cosine distance` | `search()`, `scripts/eval_rule_faqs.py` |
| `Retriever._build_keyword_index()` | `retriever.py` | KEYWORDS 静态词 → 含词块 id 倒排（惰性构建一次） | `_keyword_hits()` |
| `Retriever._keyword_hits(query)` | `retriever.py` | 静态词走倒排 + 查询相关名称线性扫描，返回 `{block_id: 命中数 × KEYWORD_BONUS}` | `search()` |
| `Retriever.search(query, heroes, top_k)` | `retriever.py` | 混合检索主入口（见下文第二章链路） | `rag_prompt.build_rag_context()` |
| `Retriever._apply_kind_quota(items, top_k)` | `retriever.py` | 按 `KIND_MAX` 分类型配额裁剪，总数不超过 `top_k` | `search()` |
| `Retriever.hero_blocks(hero)` | `retriever.py` | 按武将名倒排返回其全部语料块（攻略/相性用，保证人物召回完整） | `rag_prompt.build_*_rag_context()` |
| `Retriever._set_blocks(blocks)` | `retriever.py` | 注入语料块并重建内存索引（测试替身用） | `Retriever.blocks` |

---

## 二、语料构建链路

语料构建的单一事实源是 `src/business/rag/task_defs.py` 的 `TASKS`（共 10 个任务），UI 工作台与 `scripts/maintain_rag.py` 调度脚本共用它。所有 build 脚本共享 `rag_common.py` 的 stdout 包装、UTF-8-SIG 容错读取与原子写。

### 2.1 任务定义 task_defs.py

| 任务名 | 构建脚本 | 源文件 | 输出语料 | 期望块数 |
|--------|----------|--------|----------|----------|
| 武将语料 | `build_rag_corpus.py` | `data/heroes.json`、`data/cards.json`、`data/mjs_adjustments.json` | 武将RAG语料.json | 615 |
| 卡牌语料 | `build_card_corpus.py` | `data/cards.json` | 卡牌RAG语料.json | 49 |
| 点数花色语料 | `build_cardpts.py` | `data/card_points.json` | 卡牌点数花色语料.json | 49 |
| 装备属性语料 | `build_equip_attr.py` | `data/cards.json`、`data/equip_attrs.json`、卡牌RAG语料.json | 装备属性语料.json | 27 |
| 加强削弱语料 | `build_modify_corpus.py` | `data/cards.json`、`data/card_annotations.json` | 加强削弱语料.json | 49 |
| 元规则/术语/FAQ | `build_rule_corpus.py` | `docs/元规则整理-完整版.md` | 元规则RAG语料-章节块.json、术语表.json、FAQ裁定块.json | `snapshot` |
| 特殊机制语料 | `build_special_corpus.py` | `data/special_cards.json` | 特殊机制语料.json | 83 |
| 武将分类语料 | `build_classification_corpus.py` | `data/hero_classification.json`、`data/heroes.json` | 武将分类语料.json | 动态 |
| 组合语料 | `build_combo_corpus.py` | `data/raw_guides/jinxia/combos/` 多文件、`data/heroes.json` | 组合RAG语料.json | 动态 |
| 武将攻略语料 | `build_guide_corpus.py` | `data/raw_guides/jinxia/guides/`、`data/heroes.json`、`data/mjs_adjustments.json` | 武将攻略RAG语料.json | 动态 |

### 2.2 调度入口 maintain_rag.py

```
RagMaintenancePanel._run(args)                                    [维护面板按钮]
  -> ScriptRunner.run(sys.executable, None, ['-m', 'src.scripts.maintain_rag'] + args, root)
    ────────────────────────────────────────────────
    [子进程] maintain_rag.main()
      -> argparse.parse_args()                                    [--force/--check/--only/--keep-going/--build-index/--strict-audit]
      -> rag_audit.audit_hero_coverage(ROOT) + rag_audit.audit_version_timeline(ROOT)
         -> [有 issues 且 --strict-audit] sys.exit(1)             [人工补充清单作为门禁]
      -> audit_rule_doc.audit(doc_path, snapshot_path, root, update_snapshot=False, print_report=True)
         -> [有 ERROR/WARN 且 --strict-audit] sys.exit(1)        [元规则母本校验门禁]
      -> load_state()                                             [.rag_state.json 指纹状态]
      -> [遍历 TASKS] task_changed(task, state)
         -> file_fingerprint(p)                                   [md5+size+mtime；目录源聚合内部全部文件]
      -> [args.only 过滤] 生成 plan
      -> [args.check] summarize_counts() -> return                [只检测不执行]
      -> [每个 task] run_script(task['script'], timeout=180)
         -> subprocess.run([sys.executable, '-m', 'src.scripts.' + module], cwd=ROOT)
         ────────────────────────────────────────────────
         [孙进程] build_*_corpus.py（见 2.4）
         ────────────────────────────────────────────────
      -> verify_outputs(task)                                     [块数校验：精确匹配 / snapshot 只增 / 动态只报]
         -> [expected=='snapshot'] audit_rule_doc.snapshot_counts()
      -> [snapshot 任务且校验通过] audit_rule_doc.audit(..., update_snapshot=True)
         -> build_snapshot() -> write_snapshot()                  [刷新 .rule_doc_snapshot.json]
      -> update_state_fingerprints(plan, failed, force, state)    [失败任务及其依赖源保持旧指纹]
      -> save_state(state)
      -> [args.build_index 且无失败] subprocess.run(['-m', 'src.rag.indexer']) -> build_index()
      -> summarize_counts()
```

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `main()` | `scripts/maintain_rag.py` | 语料构建调度主流程 | 维护面板 `--only` / `--force`、`apply_rule_proposal.py` |
| `file_fingerprint(path)` | `scripts/maintain_rag.py` | 文件变更指纹；目录源聚合内部全部文件 md5 | `task_changed()` |
| `load_state()` / `save_state(state)` | `scripts/maintain_rag.py` | `.rag_state.json` 读写（损坏时按空状态重跑全部） | `main()` |
| `task_changed(task, state)` | `scripts/maintain_rag.py` | 判断任务是否需执行：任一源或脚本自身变化 | `main()` |
| `run_script(script_name, timeout)` | `scripts/maintain_rag.py` | subprocess 运行 build 脚本，返回 `(ok, output)` | `main()` |
| `verify_outputs(task)` | `scripts/maintain_rag.py` | 块数校验（精确 / snapshot 只增不删 / 动态只报） | `main()` |
| `update_state_fingerprints(...)` | `scripts/maintain_rag.py` | 失败任务依赖源保持旧指纹，防止坏语料被永久跳过 | `main()` |
| `summarize_counts()` | `scripts/maintain_rag.py` | 打印各语料块数概览 | `main()` |

### 2.3 公共基建 rag_common.py

```
build_*_corpus.py 模块顶部
  -> setup_stdout()                                              [统一 stdout UTF-8，幂等]
  -> get_script_logger(script_name)                              [文件 DEBUG+ / stderr WARNING+]
  -> load_json(path, required, label)                            [UTF-8-SIG 容错；required=True 失败 exit 1]
  -> save_json(path, data, indent=1)
     -> src.data.json_repository.atomic_write_json()             [原子写，见第三章]
  -> project_path(*parts)                                        [基于 __file__ 的绝对路径]
```

stdout 是 QProcess 进度契约：只允许协议行与面向用户的汇总，诊断信息一律走 `get_script_logger` 返回的 logger。

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `setup_stdout()` | `scripts/rag_common.py` | stdout UTF-8 包装（幂等） | 全部 build 脚本 |
| `get_script_logger(name)` | `scripts/rag_common.py` | 脚本专用 logger（`logs/rag/<name>.log`） | `maintain_rag.py`, `audit_rule_doc.py` |
| `load_json(path, required)` | `scripts/rag_common.py` | UTF-8-SIG 容错读取 JSON | 全部 build 脚本 |
| `save_json(path, data)` | `scripts/rag_common.py` | 原子写 JSON（UTF-8 / LF / indent=1） | 全部 build 脚本 |
| `HEADING_RE` / `SEPARATOR_RE` | `scripts/rag_common.py` | 元规则文档结构解析正则（三个脚本共用口径） | `build_rule_corpus.py`, `sync_rule_stats.py`, `apply_rule_proposal.py` |

### 2.4 各 build 脚本落盘

十个脚本共用同一套骨架：`setup_stdout()` -> `load_json(源)` -> 规则抽取索引字段 -> `merge_curated()`（仅卡牌/武将两个）-> 写 `.md` + `save_json()`。差异如下。

```
build_rag_corpus.py（武将语料，最复杂）
  -> load_timeline() + [TRIGGER_OVERRIDES 失效校验] + stale_overrides(timeline) 风险提示
  -> [每个有技能的武将] stamp_hero_block(overview 块, name, timeline)
     -> [每个技能] skill_block(h, s) -> extract_timing() / extract_trigger_cond()
        （TRIGGER_OVERRIDES 人工映射优先）/ extract_target() / extract_keywords() / extract_related()
  -> merge_curated() -> 写 武将RAG语料.md + save_json(武将RAG语料.json)

build_card_corpus.py -> 每卡 extract_timing/trigger/keywords/related
  -> [读旧语料] 回填 build_equip_attr 注入的装备字段（防单独重建丢失）-> merge_curated() -> 落盘

build_rule_corpus.py -> parse_rule_doc(doc_path) -> (blocks, terms, faqs, dropped)
  -> 章节块 rule_section_<章>_<节>、FAQ 块 faq_%03d（编号不回收）、术语块 term_<名称>
  -> 落盘 元规则RAG语料-章节块.json / 术语表.json / FAQ裁定块.json
     [parse_rule_doc 同时供 audit_rule_doc 复用做"解析回声"校验]

build_cardpts.py -> card_points.json（162 张，judge_rules 取判定）-> 卡牌点数花色语料.json
build_equip_attr.py -> equip_attrs.json + cards.json -> 装备属性语料.json（并向卡牌语料注入装备字段）
build_modify_corpus.py -> card_annotations.json -> 加强削弱语料.json（card_id 全匹配校验，失败 exit 1）
build_special_corpus.py -> special_cards.json -> 特殊机制语料.json
build_classification_corpus.py -> hero_classification.json + heroes.json -> 武将分类语料.json
build_combo_corpus.py -> combos/（csv + md 深解 + 手录导入，同武将对去重合并）-> 组合RAG语料.json
build_guide_corpus.py -> guides/（45 篇 md 按 ## 章节拆，单块上限 600 字）-> 武将攻略RAG语料.json
```

### 2.5 curated 合并 rag_curated.py

```
build_rag_corpus.py / build_card_corpus.py 写文件前
  -> rag_curated.merge_curated(blocks, old_json_path)
     -> [旧语料不存在或解析失败] return 0
     -> old = {block_id: block}
     -> [每个新块] curated = old[block_id].get('curated')
        -> [是 dict] 用 curated 覆盖顶层 5 个索引字段（timing/trigger_condition/keywords/related/target）
        -> 保留 curated 字段本身 -> merged += 1
     -> return merged
```

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `merge_curated(blocks, old_json_path)` | `scripts/rag_curated.py` | 重跑 build 时保留人工/LLM 精化成果 | `build_rag_corpus.py`, `build_card_corpus.py` |

---

## 三、向量索引重建

向量索引重建只有一个入口 `indexer.build_index()`，可由三条路径触发：维护面板「重建语料+索引」按钮、`maintain_rag.py --build-index` 参数、或直接命令行执行。索引集合名固定为 `mjs_rag_v1`，距离空间固定为余弦，向量化批大小 32、写入批大小 200。

```
RagMaintenancePanel._run(["--force", "--build-index"])
  -> ScriptRunner.run(...)
    ────────────────────────
    [子进程] maintain_rag.main()
      -> [语料任务全部成功] subprocess.run([sys.executable, '-m', 'src.rag.indexer'])
        ────────────────────────
        [孙进程] indexer.__main__ -> setup_logging() -> build_index(rebuild=True)
          -> load_all_blocks()                                    [1/4 读取 12 个语料 JSON]
          -> [block_id 重复] raise ValueError
          -> SentenceTransformer(EMBEDDING_MODEL, device='cpu')    [2/4 加载 bge-small-zh-v1.5]
          -> model.encode(docs, batch_size=32, normalize_embeddings=True)  [3/4]
          -> chromadb.PersistentClient(path=CHROMA_DIR)           [4/4]
          -> client.delete_collection('mjs_rag_v1')               [删除失败且集合存在 -> RuntimeError]
          -> client.get_or_create_collection('mjs_rag_v1', metadata={'hnsw:space': 'cosine'})
          -> coll.add(ids, documents, metadatas, embeddings)      [BATCH=200 分批]
          -> return (coll.count(), 'mjs_rag_v1')
        ────────────────────────
      -> [returncode == 0] print('✅ 索引重建完成')
```

增量模式（`--no-rebuild` 或 `rebuild=False`）保留集合结构与 HNSW 索引：过期块 `coll.delete()`、新增块 `coll.add()`、交集块 `coll.update()`，适用于只改了少量语料字段的场景。

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `build_index(rebuild=True)` | `rag/indexer.py` | 构建 ChromaDB 向量索引并返回 `(块数, 集合名)` | `maintain_rag.py --build-index`、`indexer.__main__`、`ai_batch --rebuild-rag-index` |
| `load_all_blocks()` | `rag/indexer.py` | 加载并规范化全部语料 | `build_index()`, `Retriever.blocks` |

---

## 四、混合检索

`Retriever.search()` 是混合检索的唯一入口：先走 Chroma 向量检索取候选，再用内存倒排做关键词兜底，两路结果按 RRF（倒数排名融合）综合排序，然后按语料类型配额裁剪到 `top_k`。关键词兜底绕过了向量侧的 `where` 过滤，因此融合后必须统一补齐武将硬过滤与版本剔除。

```
Retriever.search(query, heroes=None, top_k=None)
  -> top_k = top_k or config.TOP_K
  -> where = build_search_where(heroes)                          [is_current=true (+ hero $in)]
  -> vec = [v for v in _vector_search(query, where, n=max(top_k*2, 30))
            if v['score'] >= config.MIN_VECTOR_SCORE]            [向量检索 + 相似度下限]
  -> kw = _keyword_hits(query)                                   [关键词兜底，走内存索引]
     -> _build_keyword_index()                                    [静态 KEYWORDS 倒排，惰性一次]
     -> [查询含静态词] 倒排取块 + 1 分
     -> [查询含块元数据里的名称] 线性扫描 + 命中数分
     -> return {block_id: count * KEYWORD_BONUS}
  -> merged = {}                                                 [RRF 融合]
     -> 向量按分数降序排名: item['rrf'] = 1/(RRF_K + rank), source='vector'
     -> 关键词按分数降序排名: item['rrf'] += 1/(RRF_K + rank)
        -> [两边都命中] source='vector+kw'
        -> [仅关键词] 从内存索引补 text/metadata, source='keyword'
  -> merged = {剔除 is_current=='false' 与 hero 不在 heroes 的块}  [补齐硬过滤]
  -> kw_only 按 rrf 降序，删除超出 MAX_KEYWORD_ONLY 的纯关键词块   [防数量失控]
  -> ranked = sorted(merged.values(), key=rrf, reverse=True)
  -> _apply_kind_quota(ranked, top_k)                            [分类型配额裁剪]
     -> [counts[kind] >= KIND_MAX[kind]] 跳过
     -> [len(out) >= top_k] 截断
     -> return out
```

`hero_blocks(hero)` 是独立路径：直接查武将名倒排返回该武将的全部块，保证生成某武将攻略时人物语料必然被召回，不经过 RRF。

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `Retriever.search(query, heroes, top_k)` | `rag/retriever.py` | 混合检索主入口 | `rag_prompt.build_rag_context()`, `build_synergy_rag_context()`、`retriever.__main__` |
| `build_search_where(heroes)` | `rag/retriever.py` | 构造 Chroma where 条件 | `Retriever.search()` |
| `Retriever._vector_search()` | `rag/retriever.py` | bge 编码 + Chroma 查询 | `search()`, `eval_rule_faqs.run_eval()` |
| `Retriever._keyword_hits()` | `rag/retriever.py` | 关键词兜底命中加分 | `search()` |
| `Retriever._apply_kind_quota()` | `rag/retriever.py` | 分类型配额裁剪 | `search()` |
| `Retriever.hero_blocks(hero)` | `rag/retriever.py` | 按武将名取全部块 | `rag_prompt.build_*_rag_context()` |

---

## 五、RAG 注入 AI 生成

本模块只负责"把语料拼成提示词区块"这一小段，AI 调用、JSON 提取与 mart 写回全部在 [call_graph_ai_batch.md](./call_graph_ai_batch.md)。注入开关由环境变量 `RAG_ENABLED`（`--no-rag` 覆盖）优先、其次 `config.env` 决定；任何检索异常一律降级为空串，不阻断生成。

```
[子进程] ai_batch.main() -> run_guide_generation()
  -> AIBatchGenerator.generate_guide(hero)
     -> load_prompt(GUIDE_PROMPT_FILE)
     -> prompt_utils.build_guide_prompt(hero)
        -> rag_prompt.build_rag_context(hero)
           -> is_rag_enabled()                                    [env 优先，其次 config]
           -> [关闭] return ""
           -> _get_retriever()                                    [进程内单例]
              -> Retriever() -> load_all_blocks() / model / collection  [首次懒加载]
           -> retriever.hero_blocks(hero_name)                    [本武将语料，必召回]
           -> 技能名 + 机制词 -> query 拼装
           -> retriever.search(query, top_k=RAG_TOP_K)            [混合检索，见第四章]
              -> post-filter: combo 块按 metadata.heroes 列表过滤，hero 块按名称过滤
           -> _format_rag_chunks(blocks, extra, budget)           [官方/社区两池，整块丢弃不截断]
        -> [_rag_enabled()] rule_summary.load_card_system()       [卡牌体系段兜底防牌名串味]
        -> [未启用 RAG 且无语料] rule_summary.load_core_rules()   [完整核心规则摘要兜底]
     -> _request_content(messages, temperature=0.7)               [API 或 Playwright]
        -> _call_api() / DeepSeekBrowserSession.send_and_wait()
     -> extract_json(content) -> validate_guide()
     -> _commit_generation_batch() -> _save_json()                [mart 原子写回]
     -> [RAG 降级] _report_rag_degradation()
        -> rag_prompt.take_degraded_reason()                      [取出并清空降级原因，只输出一次]
```

相性生成链路同构：`build_synergy_prompt()` -> `build_synergy_rag_context(hero_a, hero_b)` -> `retriever.hero_blocks()` ×2 + `retriever.search()` ×2（双查询各取半数去重合并）-> `_format_rag_chunks()`。

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `build_rag_context(hero, max_chars)` | `scraper/ai/rag_prompt.py` | 检索并格式化攻略语料区块，失败返回空串 | `prompt_utils.build_guide_prompt()` |
| `build_synergy_rag_context(a, b, max_chars)` | `scraper/ai/rag_prompt.py` | 双武将语料检索与格式化 | `prompt_utils.build_synergy_prompt()` |
| `_get_retriever()` | `scraper/ai/rag_prompt.py` | 进程内 Retriever 单例 | `build_*_rag_context()` |
| `is_rag_enabled()` | `scraper/ai/rag_prompt.py` | RAG 开关判定（env 优先） | `ai_batch.main()`, `build_*_prompt()` |
| `take_degraded_reason()` | `scraper/ai/rag_prompt.py` | 取出并清空降级原因 | `generation._report_rag_degradation()` |
| `_format_rag_chunks(blocks, extra, budget)` | `scraper/ai/rag_prompt.py` | 官方/社区独立预算池，combo 优先 | `build_*_rag_context()` |

> 【假设】`build_synergy_rag_context` 与 `_format_rag_chunks` 的具体内部实现细节来自 `call_graph_ai_batch.md` 的描述（该文档基线同为 2026-09-04），本文档未逐行复读 `rag_prompt.py` 后半段。若两份文档冲突，以源码为准。

---

## 六、索引精化（三层架构，重点）

索引精化把"补全卡牌/武将语料的四个索引字段（timing / trigger_condition / keywords / related）"这件事拆成三层：对话框只负责渲染与交互确认；`SuggestController` 负责 LLM 建议的线程生命周期与取消善后；`RefinementSession` 负责清单三池归属、磁盘/LLM 双基线与持久化写回。三层可各自独立测试，互不持有对方引用。

### 6.1 会话状态层 refinement_session.py

```
RefinementSession.__init__(corpus_dir)
  -> scan_blocks(corpus_dir)                                      [一次扫描三分类]
  -> self._pending / _curated / _normal                           [三池清单]
  -> self._total = len(_pending)                                  [进度条分母，不随保存/跳过变化]
  -> [curated 块] _row_states[block_id] = "refined"
  -> [normal 块] _row_states[block_id] = "generated"
  -> [全部块] _saved_baseline[block_id] = {field: "\n".join(fields)}  [磁盘基线]

note_suggested(block, update)                                     [批量建议结果登记，不回填编辑器]
  -> _llm_baseline[block_id] = {field: 文本}
  -> _row_states[block_id] = "suggested"

collect_update(block_id, texts)                                   [字段文本 -> RefinementUpdate]
  -> [与磁盘基线全一致] return None                                [no-op 判定]
  -> [与 LLM 基线完全一致] method = "llm"；否则 "manual"
  -> return RefinementUpdate(..., method, updated_at)

sync_saved(block, update)                                         [保存成功后的内存同步]
  -> _saved_baseline[block_id] = 新基线
  -> _llm_baseline.pop(block_id)
  -> _row_states[block_id] = "refined"
  -> block.fields / missing / method / updated_at 更新
  -> [在 _pending] 移出 -> _curated.append
  -> [在 _normal]  移出 -> _curated.append

apply_updates(updates_by_file) -> (saved, errors)                 [按语料文件分组批量写回]
  -> apply_curated(corpus_dir, updates, fname)                    [底层原子写]
     -> [OSError/ValueError] errors[fname] = msg -> continue       [失败文件不迁移其任何块]
  -> [每个成功块] sync_saved(block, update) -> saved += 1

skip_block(block)                                                 [移出清单，清理基线与行状态]
clear_curated_block(block)                                        [写盘成功前不做任何内存变更]
  -> clear_curated(corpus_dir, block_id, fname)
  -> _curated 移除 -> _llm_baseline.pop -> _row_states.pop
  -> [有 missing] _pending.append + state="pending"
  -> [无 missing] _normal.append + state="generated"
```

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `RefinementSession.__init__(corpus_dir)` | `business/rag/refinement_session.py` | 扫描三池并建立双基线 | `IndexRefinementDialog.__init__()` |
| `blocks_for_scope(scope)` | 同上 | 按 pending/curated/all 取清单 | `IndexRefinementDialog._scope_blocks()` |
| `note_suggested(block, update)` | 同上 | 登记批量建议结果 | `IndexRefinementDialog._on_suggest_result()` |
| `collect_update(block_id, texts)` | 同上 | 收集字段文本并判定 no-op 与 method | `IndexRefinementDialog._collect_update()` |
| `sync_saved(block, update)` | 同上 | 保存后内存同步与池迁移 | `apply_updates()` |
| `apply_updates(updates_by_file)` | 同上 | 分组批量写回 | `_save_current()`, `_save_all()` |
| `skip_block(block)` / `clear_curated_block(block)` | 同上 | 跳过 / 取消精化 | `_skip_current()`, `_clear_curated()` |

### 6.2 服务层 refinement_service.py

```
scan_blocks(corpus_dir) -> {"pending": [...], "curated": [...], "normal": [...]}
  -> [遍历 REFINABLE_FILES = (卡牌RAG语料.json, 武将RAG语料.json)]
     -> json.loads(path)
     -> [武将语料且无 skill] 跳过 overview 块
     -> curated = block.get("curated")
        -> [是 dict] fields 以 curated 内容为权威 -> result["curated"].append(_to_block(..., method, updated_at))
        -> [否] values = {f: block.get(f) for f in INDEX_FIELDS}
           -> missing = [f for f in INDEX_FIELDS if not values[f]]
           -> [missing 非空] result["pending"].append
           -> [全非空] result["normal"].append

suggest_one(block, generator) -> RefinementUpdate | None          [单块 LLM 建议，公开接口]
  -> messages = [system: REFINEMENT_SYSTEM_PROMPT, user: 语料类型/名称/原文]
  -> generator.complete(messages, temperature=0.2)                 [AIBatchGenerator]
     -> [异常] logger.warning -> return None
  -> extract_json(content)
     -> [ValueError/TypeError] return None
  -> _to_update(data, method="llm")                               [字段规范化：str->list、去空、截断上限]

apply_curated(corpus_dir, updates, fname) -> int
  -> json.loads(path) -> [非 list] raise ValueError
  -> by_id = {block_id: block}
  -> [block_id 不存在] raise ValueError
  -> [每块] 更新顶层 4 个索引字段 + 新增 curated 字段（含 method/updated_at）
  -> _atomic_json_write(path, data) -> atomic_write_json(path, data, indent=1)

clear_curated(corpus_dir, block_id, fname) -> bool                [删除 curated 字段；本无 curated 返回 False]
build_generator(profile_name) -> AIBatchGenerator | None          [供应商需 Key 且缺 Key 返回 None]
```

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `scan_blocks(corpus_dir)` | `business/rag/refinement_service.py` | 一次扫描三分类 | `RefinementSession.__init__()` |
| `list_pending()` / `list_curated()` / `list_normal()` | 同上 | 单池便捷读取 | `RagMaintenancePanel.refresh()`, `audit_service.audit_summary()` |
| `suggest_one(block, generator)` | 同上 | 单块 LLM 建议 | `SuggestWorker.run()`, `generate_suggestions()` |
| `generate_suggestions(pending, generator)` | 同上 | 逐块同步建议（测试与脚本用） | `suggest_controller`（间接）、测试 |
| `apply_curated(corpus_dir, updates, fname)` | 同上 | 写回 curated 并原子保存 | `RefinementSession.apply_updates()` |
| `clear_curated(corpus_dir, block_id, fname)` | 同上 | 取消精化 | `RefinementSession.clear_curated_block()` |
| `build_generator(profile_name)` | 同上 | 构造 LLM 生成器 | `IndexRefinementDialog._generator()`, `propose_rule_changes.py` |
| `INDEX_FIELDS` / `REFINABLE_FILES` | 同上 | 4 个索引字段名 / 2 个可精化语料文件 | 会话层、对话框层 |

### 6.3 线程编排层 suggest_controller.py

```
SuggestController.start(blocks, generator, single=False)
  -> [_running] return
  -> self._running=True; _single=single; _total=len(blocks); _done=0; _failed=[]
  -> SuggestWorker(list(blocks), generator)                       [parent=None：dialog 销毁不连带析构]
  -> worker._single = single
  -> worker.result_ready.connect(self._on_result_ready)
  -> worker.finished.connect(self._on_worker_finished)
  -> worker.finished.connect(worker.deleteLater)                  [自回收]
  -> worker.start()

SuggestWorker.run()                                               [QThread 后台]
  -> LIVE_WORKERS.add(self)                                       [模块级集合持有，防 GC 析构]
  -> [每块] _cancelled -> break
  -> update = suggest_one(block, self._generator)                 [business/rag/refinement_service]
  -> self.result_ready.emit(block, update)
  -> finally: LIVE_WORKERS.discard(self)

_on_result_ready(block, update)
  -> _done += 1
  -> [update is None] _failed.append(block)
  -> self.result_ready.emit(block, update, self._single)          [转发给对话框]

_on_worker_finished()
  -> _worker = None
  -> _release_generator(generator) -> generator.close()
  -> _running = False; _cancelled = False                         [先复位再发信号]
  -> [非取消路径] self.finished.emit(self._single)                [取消/关闭不发 finished]

cancel_and_shutdown()                                             [关闭/取消时调用]
  -> _cancelled = True; _running = False; _generator = None
  -> worker._cancelled = True
  -> worker_generator.cancel() -> _release_generator()
  -> worker.wait(1000)
  -> [仍 isRunning] _zombies.append(worker)                       [僵尸持有，防 QThread 运行中析构崩溃]
     -> worker.finished.connect(worker.deleteLater) + _on_zombie_finished
```

对话框 `reject()` 在 `is_running` 时先调 `cancel_and_shutdown()`，再做脏确认——单块建议运行时用户可能有未保存编辑，必须一并确认。

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `SuggestWorker.run()` | `business/rag/suggest_controller.py` | 逐块调用 `suggest_one` 并发信号 | Qt 线程调度 |
| `SuggestController.start(blocks, generator, single)` | 同上 | 启动建议线程并连接信号链 | `_suggest_current()`, `_suggest_all()` |
| `SuggestController.cancel_and_shutdown()` | 同上 | 中止在途建议并释放生成器 | `IndexRefinementDialog.reject()` |
| `SuggestController._on_result_ready()` | 同上 | 计数 + 失败收集 + 转发 | `SuggestWorker.result_ready` |
| `SuggestController._on_worker_finished()` | 同上 | 收尾与 generator 释放 | `SuggestWorker.finished` |
| `is_running` / `total` / `done` / `failed` | 同上 | 状态属性供按钮可用性判定 | 对话框 `_update_overview()` |

### 6.4 对话框层 index_refinement_dialog.py

```
RagMaintenancePanel._open_refinement()
  -> IndexRefinementDialog(root/data/rag_corpus, parent)
     -> RefinementSession(corpus_dir)                             [状态层，见 6.1]
     -> SuggestController(self)                                   [线程编排，见 6.3]
     -> controller.result_ready.connect(self._on_suggest_result)
     -> controller.finished.connect(self._on_suggest_finished)
     -> _setup_ui(): PageHeader + _build_overview_bar() + QSplitter(_build_table_pane(), _build_editor_pane())
     -> _refresh_table()

模式切换  _set_scope("pending"|"curated"|"all")
  -> _table.setCurrentCell(-1, -1)                                [先清空选择，保证重填后能触发信号]
  -> _refresh_table()
     -> _scope_blocks() -> RefinementSession.blocks_for_scope(scope)
     -> _matches(block)                                           [类型筛选 + 搜索防抖 250ms]
     -> _detail_text(block)                                       [已精化显来源/时间；待精化显缺失字段]
     -> _row_states[block_id] -> _ROW_STATE_TEXT / _ROW_STATE_COLOR

LLM 建议（当前） _suggest_current()
  -> _generator() -> build_generator(None)                        [未配置 API -> QMessageBox.warning]
  -> SuggestController.start([self._current], generator, single=True)

LLM 建议（全部） _suggest_all()
  -> [_dirty] _confirm_discard()
  -> SuggestController.start(self._pending, generator)

_on_suggest_result(block, update, is_single)                      [主线程接收]
  -> is_current = is_single and 当前块 == block
  -> [不在 pending 池且非单块当前块] return                        [防止结果"复活"已跳过的块]
  -> [update 非 None]
     -> RefinementSession.note_suggested(block, update)
     -> _refresh_row_state(block)
     -> [单块且当前] _fill_suggestion(block, update) -> RefinementSession.record_llm_baseline()
  -> [单块失败] QMessageBox.warning
  -> _update_overview()

_on_suggest_finished(is_single)
  -> [批量] _finish_suggest_all()                                 [失败汇总按"仍在待精化池"过滤]
  -> _update_overview()                                          [按钮恢复统一走此处]

保存当前 _save_current()
  -> _collect_update() -> RefinementSession.collect_update(block_id, texts)
     -> [None] show_toast("无修改，未保存")
  -> RefinementSession.apply_updates({block.corpus: {block_id: update}})
     -> [errors] QMessageBox.critical
  -> _refresh_table() -> show_toast

保存全部 _save_all()
  -> QMessageBox.question 确认
  -> [每个待精化块] is_current ? _collect_update() : RefinementSession.baseline_update(block_id)
     -> [update 为空或全空字段] skipped += 1
     -> updates_by_file.setdefault(block.corpus, {})[block_id] = update
  -> RefinementSession.apply_updates(updates_by_file)
  -> [逐文件错误] QMessageBox.critical

跳过 _skip_current() -> RefinementSession.skip_block(block)
取消精化 _clear_curated()
  -> QMessageBox.question 二次确认
  -> RefinementSession.clear_curated_block(block)
     -> [OSError/ValueError] QMessageBox.critical -> return      [写盘前无内存变更，可安全重试]

reject()
  -> [_controller.is_running] _controller.cancel_and_shutdown()
  -> [_dirty 且当前块] _confirm_discard()
  -> super().reject()
```

清单行状态文案映射：`pending`→○ 未处理、`suggested`→◉ 已建议、`modified`→✎ 已修改、`refined`→✓ 已精化、`generated`→○ 已生成。字段卡片状态：`empty`→待填写、`llm`→LLM 建议、`saved`→已精化、`manual`→已修改。

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `IndexRefinementDialog.__init__(corpus_dir, parent)` | `ui/maintenance/index_refinement_dialog.py` | 装配状态层 + 线程编排 + UI | `RagMaintenancePanel._open_refinement()` |
| `_set_scope(scope)` | 同上 | 切换待精化/已精化/全部范围 | 总览条模式按钮 |
| `_refresh_table()` / `_refresh_row_state(block)` | 同上 | 清单渲染与单行状态刷新 | 模式切换、保存、建议结果 |
| `_load_current(block)` / `_clear_editor()` | 同上 | 工作区加载与清空 | `_on_table_selected()` |
| `_field_state(field)` / `_refresh_field_states()` | 同上 | 字段卡片状态判定与着色 | `_on_field_edited()` |
| `_suggest_current()` / `_suggest_all()` | 同上 | 单块/批量 LLM 建议入口 | 操作按钮 |
| `_on_suggest_result()` / `_on_suggest_finished()` | 同上 | 接收建议结果与收尾 | `SuggestController` 信号 |
| `_save_current()` / `_save_all()` | 同上 | 单块/批量保存写回 | 操作按钮 |
| `_skip_current()` / `_clear_curated()` | 同上 | 跳过 / 取消精化 | 操作按钮 |
| `_update_overview()` | 同上 | 进度、统计与按钮可用性统一更新 | 几乎所有交互后 |

---

## 七、元规则 T0 文档维护

维护对象是 `docs/元规则整理-完整版.md`（全项目唯一规则权威母本，只增不删、机器校验）。UI 层 `RuleDocPanel` 四个子页签分别对应审计、数据段同步、提案、疑难登记；脚本执行统一经 `ScriptRunner`（`src/business/common/script_runner.py`，由 `src/ui/shared/widgets.py` 再导出）以 QProcess 子进程方式跑，纯函数（解析、读写、校验）集中在 `src/business/rag/rule_doc_service.py` 与 `src/business/maintenance/rule_doc_ops_service.py`。

### 7.1 调用链总览

```
RuleDocPanel(root)
  -> ScriptRunner(self) -> output.connect(_append_log) / finished.connect(_on_runner_finished)
  -> _ensure_ops_handler(root)                             [logs/rule_doc_ops.log 执行轨迹，与 app.log 分离]
  -> _setup_ui(): PageActionBar + QTabWidget 4 页签
  -> refresh()                                             [本地只读：提案/疑难/差异表，不发进程]

【页签 1 文档状态】refresh_audit()
  -> _run_script(["audit_rule_doc.py"], _on_audit_finished)
     -> ScriptRunner.run(PYTHON, None, ['-m', 'src.scripts.audit_rule_doc'], root)
       ──────────────────────────────
       [子进程] audit_rule_doc.main() -> audit(...)
         -> build_rule_corpus.parse_rule_doc(doc_path) -> (blocks, terms, faqs, dropped)
         -> load_snapshot()
         -> 1 解析回声 / 2 表格列数 / 3 块 ID 唯一 / 4 章节结构指纹 /
            5 ID 稳定性 / 6 FAQ 编号 / 7 确认状态 / 8 交叉引用 /
            9 已定稿块指纹 / 10 数据段一致性（调 sync_rule_stats.diff_sections）
         -> _print_report() -> "汇总：ERROR n / WARN n / INFO n"
       ──────────────────────────────
  -> _on_runner_finished(code) -> _on_finished(code, ...) -> _on_audit_finished()
     -> rds.parse_audit_output(self._last_output())        [正则解析 [ERROR|WARN|INFO] 与"汇总："行]
     -> rds.audit_issue_counts(issues)                     [ERROR/WARN/INFO 计数]
     -> 文档状态表 3 行 + ERROR/WARN 明细表（含"去同步"跳转按钮）
  -> [_pending_sync] refresh_diffs()                        [串行：audit 完成后才 sync]

【页签 2 数据段差异】refresh_diffs()
  -> rds.sync_json_path(root)                              [src/scripts/.sync_rule_stats_report.json]
  -> _run_script(["sync_rule_stats.py", "--json", path], _on_sync_finished, sentinel_codes={1})
     ──────────────────────────────
     [子进程] sync_rule_stats.main()
       -> load_data() -> diff_sections(doc_text, data, only)
          -> find_section() -> table_rows_in() -> _match_rows()
          -> gen_card_system_rows / gen_hero_stats_rows / gen_timing_candidates /
             gen_limit_checks / gen_faq_rows                [0.1/0.2/3.1/3.2/3.5/5.2 段期望值]
       -> save_json(args.json, issues, indent=2) -> exit(1 if issues else 0)
     ──────────────────────────────
  -> _on_sync_finished() -> rds.parse_sync_diff(path)
     -> 差异表 7 列（应用勾选/段/行号/类型/摘要/确认值/操作）
     -> 类型映射：full=全自动 / candidate=候选 / checkpoint=校验点
     -> _compose_next_step() 给出状态驱动下一步建议

_apply_diffs()                                             [应用已确认差异]
  -> _collect_confirmed_rows()
     -> [每勾选行] validate_confirmed_row(diff, new)        [rule_doc_ops_service 校验]
        -> [非空 / 完整表格行 / 列数一致] 否则 QMessageBox.warning
  -> QMessageBox.question 确认
  -> rule_doc_ops_service.save_confirmed_diffs(root, rows)  [atomic_write_json 写确认清单]
  -> _run_script(["sync_rule_stats.py", "--apply-json", path], _on_apply_json_finished,
                 failure_codes={1: ..., 2: ...})
     ──────────────────────────────
     [子进程] apply_confirmed(confirmed, doc_text)
        -> [预检全部行：行号越界 / 当前行 != old / 空值 / 非表格行 / 列数不一致]
        -> [有 errors] 整批拒绝，返回原文本（文档零副作用）
        -> _atomic_write_text(doc) + append_changelog() + refresh_snapshot()
        -> exit(0 成功 / 1 预检失败 / 2 前置失败)
     ──────────────────────────────
  -> _on_apply_json_finished() -> refresh_diffs()           [成功行消失，失败行保留]

【页签 3 提案工作台】_refresh_proposals()
  -> rds.list_proposals(root)                              [docs/archive/proposals/CP-*.json 倒序 + 状态统计]
_generate_proposal()
  -> _run_script(["propose_rule_changes.py", "--no-llm"], _refresh_proposals)
     ──────────────────────────────
     [子进程] propose_rule_changes.main()
       -> collect_diff_rows()（复用 diff_source_data）或 --changes-json
       -> build_generator()                                [business/rag/refinement_service]
       -> generate_proposal_items(rows, doc_text, generator) -> extract_json()
          -> [无 generator / 异常] 降级占位提案（status=pending, type=none）
       -> next_proposal_id(out_dir)                        [CP-YYYY-MM-DD-NN]
       -> 写 CP-*.json + render_md() -> CP-*.md
     ──────────────────────────────
_load_proposal_detail()
  -> rds.parse_proposal(path)
  -> 提案条目表 6 列（提案号/类型/目标/建议文本/状态/操作[查看][确认]）
  -> [类型映射] faq_new=新增FAQ / faq_revise=修订FAQ / term_new=新增术语 /
               row_revise=修订表格行 / section_new=新增小节 / none=无需动文档
_open_detail_dialog(row) -> ProposalDetailDialog
  -> rds.doc_target_line(doc_path, item)                   [faq_编号定位 | N | 行；row_revise 精确匹配 old_text]
  -> rds.doc_section_context(doc_path, target)             [FAQ ±3 行 / 小节号标题后若干行]
  -> build_diff_rows() -> rows_to_html()                   [shared/rich_diff 差异对比]
  -> [faq_revise] rds.build_faq_revise_row(local, official) [与脚本实合入共用同一替换规则]
_open_confirm_dialog(row) -> ProposalItemConfirmDialog
  -> choice() -> (status, edited_text)
  -> rds.update_proposal_item(root, path, item_id, status, edited_text)
     -> [status 不在 VALID_PROPOSAL_STATUSES] raise ValueError
     -> [item_id 不存在] raise ValueError
     -> atomic_write_json(path, data)                      [原位更新提案条目]
_apply_proposal()
  -> _run_script(["apply_rule_proposal.py", "--proposal", path], _refresh_proposals)
     ──────────────────────────────
     [子进程] apply_rule_proposal.main()
       -> apply_proposal(doc_text, proposal)               [仅 status=approved/revised]
          -> _apply_faq_new / _apply_faq_revise / _apply_term_new /
             _apply_row_revise / _apply_section_new        [APPLYERS 分派]
       -> [errors] 未写回文档，exit 1
       -> 写文档 -> run_audit_strict()                     [audit --strict，失败回滚原文本]
       -> run_maintain_rules()                             [maintain_rag.py --only 元规则]
       -> append_changelog() -> archive_proposal()         [提案追加 .merged 标记]
     ──────────────────────────────

【页签 4 疑难登记】_refresh_pending() -> rds.load_pending(root)   [docs/rule_doc_pending.json]
_add_pending() -> QInputDialog -> rds.add_pending(root, desc, involved)
_to_proposal() -> rds.pending_to_proposal(root, item["id"])
  -> 生成 CP-日期-Pxxx.json（type=faq_new, target=5.2, status=pending）
  -> 疑难条目 status 置 "proposed"
```

### 7.2 纯函数清单（rule_doc_service.py）

| 函数 | 作用 | 主要调用方 |
|------|------|-----------|
| `parse_audit_output(text)` | 解析 audit 输出为 `[{level, message}]`（含 SUMMARY 汇总行） | `RuleDocPanel._on_audit_finished()` |
| `audit_issue_counts(issues)` | 统计 ERROR/WARN/INFO 数量 | 文档状态页摘要 |
| `parse_sync_diff(path)` | 读取 `sync_rule_stats.py --json` 差异项 | `RuleDocPanel._on_sync_finished()` |
| `sync_json_path(root)` / `confirmed_diff_path(root)` | 差异报告 / 确认清单路径（均在 `src/scripts/` 下） | 差异页与 `_apply_diffs()` |
| `list_proposals(root)` | 列出 `docs/archive/proposals/CP-*.json`（倒序 + 状态统计） | 提案工作台下拉 |
| `parse_proposal(path)` | 读取提案 JSON | `_load_proposal_detail()` |
| `doc_target_line(doc_path, item)` | 按 faq 编号/old_text 定位文档当前行 | `ProposalDetailDialog`, 脚本 |
| `doc_section_context()` / `doc_line_at()` / `doc_context_around()` | 按小节号/行号取文档上下文（带 mtime 缓存） | 三个详情对话框 |
| `build_faq_revise_row(line, text)` | FAQ 行原位修订替换规则（仅换裁定列 `cells[2]`） | UI 预览与 `apply_rule_proposal.py` 共用 |
| `update_proposal_item(root, path, item_id, status, edited_text)` | 原位更新提案条目（原子写，非法状态抛 ValueError） | `ProposalItemConfirmDialog` |
| `load_pending(root)` / `add_pending(root, desc, involved, source)` | 疑难登记读/增 | 疑难登记页 |
| `pending_to_proposal(root, pending_id)` | 疑难转 FAQ 新增提案并置 `status=proposed` | `_to_proposal()` |
| `parse_doc_chapter7(doc_path)` | 只读解析文档第 7 章疑难登记表 | 展示/审计 |

### 7.3 业务操作服务（rule_doc_ops_service.py）

| 函数 | 作用 | 调用方 |
|------|------|--------|
| `validate_confirmed_row(diff, new)` | 校验确认值：非空、完整表格行、列数与原文一致 | `RuleDocPanel._collect_confirmed_rows()` |
| `save_confirmed_diffs(root, rows)` | 确认清单原子写盘（`--apply-json` 输入） | `RuleDocPanel._apply_diffs()` |

### 7.4 脚本职责速查

| 脚本 | 命令示例 | 退出码语义 |
|------|---------|-----------|
| `audit_rule_doc.py` | `--strict` / `--update-snapshot` | `--strict` 下有 ERROR/WARN 返回 1 |
| `sync_rule_stats.py` | `--json out.json` / `--apply` / `--apply-json` | 1=有差异或应用预检失败，2=前置失败 |
| `propose_rule_changes.py` | `--no-llm` / `--changes-json` | 无变更返回 0（无输出提案） |
| `apply_rule_proposal.py` | `--proposal CP-*.json` | 1=合入错误或 audit 未通过（已回滚） |
| `eval_rule_faqs.py` | `--generate` / `--top-k 5` | 全部命中返回 0，否则 1 |
| `diff_source_data.py` | `--old data/backups` | 被 `propose_rule_changes.py` 复用 |
| `migrate_excel_to_json.py` | `--only points` | 卡牌点数应急导入通道 |

`eval_rule_faqs.py` 用 RAG 检索评估 FAQ 可命中率：评估集 `data/rag_evals/rule_faq_eval.json`，`run_eval()` 对每题调 `Retriever._vector_search(question, where={'kind': 'faq'}, n=...)`，断言期望 `block_id` 出现在 top-k 内，零 LLM 成本，可进 CI。

---

## 八、数据源编辑与原子写回

五个维护面板的写路径统一委托给 `src/business/maintenance/corpus_services.py` 的薄服务（写操作意图经服务入口，读写规则仍集中在仓储的原子写/回滚实现），最终落盘到 `src/data/json_repository.py` 的 `atomic_write_json()`。保存后统一发 `data_changed` 信号，由 `RagMaintenancePanel` 转发并刷新语料状态，把对应任务标为「待重建」。

### 8.1 原子写原子性保证

```
atomic_write_json(path, data, indent=2)                        [src/data/json_repository.py]
  -> path.parent.mkdir(parents=True, exist_ok=True)
  -> fd, temporary = tempfile.mkstemp(prefix=".<stem>.", suffix=".tmp", dir=path.parent)
  -> os.fdopen(fd, 'w', encoding='utf-8', newline='\n')
     -> json.dump(ensure_ascii=False, indent) -> write("\n") -> flush() -> os.fsync()
  -> Path(temporary).replace(path)                             [同目录原子替换]
  -> [异常] Path(temporary).unlink(missing_ok=True) -> raise   [原文件保持不变]
```

`JsonRepository` 基类提供 `_read_root()`（加 RLock 读盘 + 异常分级收集 `DataIssue`）、`save_payload()`（加锁原子写）、`_snapshot()` / `_restore()` / `_save_or_rollback()`（写前内存快照，失败回滚避免"看似失败、实际已变"的脏状态）。

### 8.2 四个薄服务

| 服务类 | 底层仓储 | 写入口 | 说明 |
|--------|----------|--------|------|
| `CardPointsService` | `CardPointsRepository` | `add_card` / `replace_card` / `delete_card` / `add_rule` / `update_rule` / `delete_rule` | 方法即落盘 |
| `SpecialCardsService` | `SpecialCardRepository` | `add_item` / `update_item` / `delete_item` | 方法即落盘 |
| `ClassificationService` | `HeroClassificationRepository` | `save` / `add_category` / `update_category` / `delete_category` / `set_counter_chain` / `set_hero_categories` | **内存修改 + 显式 save**，语义与其余三个不同 |
| `ComboService` | `ComboManager` | `save_manual_combo` / `delete_combo` | 实战配队手工维护，归 module_peak_combos |

四个服务都提供 `repository` 属性供面板只读查询透传，避免为纯展示路径复制大量转发方法。

### 8.3 面板写路径

```
CardPointsPanel._add_card()
  -> _ensure_writable()                                       [repository.available 检查]
  -> CardPointEditDialog(None, self).exec()
  -> run_edit_dialog(dialog, lambda: self._service.add_card(dialog.item()),
                     parent, success_message, on_retry=self.reload_data)
     ─── src/ui/shared/persist.run_edit_dialog：模态编辑 -> 确认后保存 -> 失败重试（默认 3 次）
     -> CardPointsService.add_card(item) -> CardPointsRepository.add_card(item)
        -> _save_or_rollback(snapshot) -> save() -> save_payload() -> atomic_write_json()
        -> [失败] _restore(snapshot) -> raise
  -> [saved] _refresh_cards() + self.data_changed.emit()
  -> [异常] QMessageBox.critical + reload_data()               [仓库已回滚内存，界面与磁盘重新对齐]

_edit_card() -> replace_card(old_name, old_suit, old_point, item)   [单步替换，旧键可能变化]
_delete_card() -> delete_card(name, suit, point)                   [QMessageBox 二次确认]
_add_rule() / _edit_rule() / _delete_rule()                        [同上模式]
_reload_data() -> repository.load() -> [error] 禁用全部写按钮 + PageActionBar 告警

EquipAttrsPanel._save()
  -> _collect()                                               [逐行校验：细分类型/攻击范围/距离修正，含行号原因]
     -> [非法] ValueError -> QMessageBox.warning -> return
  -> [每件] get_equip(name) is None ? add_equip(item) : update_equip(item)
  -> [异常] logger.exception + QMessageBox.critical + reload_data()
  -> data_changed.emit() + show_toast()

SpecialCardsPanel / HeroClassificationPanel（物理位于 src/ui/library/）
  -> data_changed 信号 -> RagMaintenancePanel._on_child_changed()
     -> refresh() + self.data_changed.emit()                   [标记对应语料任务「待重建」]

HeroClassificationPanel（LLM 建议归类，与索引精化共用同一 LLM 通道）
  -> _HeroCategoryWorker(QThread).run()
     -> classification_suggest.suggest_hero_categories(hero, skills_text, position, categories, generator)
        -> messages = [system: 分类器提示词, user: 武将/定位/技能/可选分类清单]
        -> generator.complete(messages, temperature=0.2)
        -> extract_json(content) -> [仅保留清单内名称，去重保序]
        -> [失败] return None
  -> [LIVE_WORKERS 持有] result_ready -> set_checked() 回填勾选（不发信号，手动触发归类变更）
  -> focus_unclassified()                                      [审计横幅跳转定位]
```

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `atomic_write_json(path, data, indent)` | `data/json_repository.py` | mkstemp + fsync + replace 原子写 | 全部仓储、`refinement_service`、`rule_doc_service`、`rag_common.save_json` |
| `JsonRepository._read_root()` | 同上 | 加锁读盘 + 异常分级 | 四个仓储 `load()` |
| `JsonRepository.save_payload()` | 同上 | 加锁原子写 | 四个仓储 `save()` |
| `JsonRepository._save_or_rollback(snapshot)` | 同上 | 写失败回滚内存 | CRUD 写入口 |
| `CardPointsService.*` | `business/maintenance/corpus_services.py` | 卡牌点数写路径 | `CardPointsPanel` |
| `SpecialCardsService.*` | 同上 | 专属牌写路径 | `SpecialCardsPanel` |
| `ClassificationService.*` | 同上 | 武将分类写路径（内存 + 显式 save） | `HeroClassificationPanel` |
| `suggest_hero_categories(...)` | `business/maintenance/classification_suggest.py` | 武将分类 LLM 建议 | `HeroClassificationPanel._HeroCategoryWorker` |
| `run_edit_dialog(dialog, persist, ...)` | `ui/shared/persist.py` | 模态编辑 + 保存失败重试循环 | `CardPointsPanel` |

> 【假设】`SpecialCardsPanel` 与 `HeroClassificationPanel` 的完整方法清单（如 `add_item` 调用细节、`reload_data` 内部实现）未逐行复读，本文档只覆盖它们与本模块相关的写路径与 `data_changed` 联动。两文件物理位置已确认为 `src/ui/library/`。

---

## 九、审计与语料状态

审计分两级：`audit_service.py` 提供 UI 用的结构化审计（`AuditIssue` 列表，可跳转）与脚本共用的校验收集函数；`rag_audit.py` 是 CLI 版人工补充清单，被 `maintain_rag.py` 在构建前作为门禁调用。两者共享同一批 `collect_*` 收集函数，避免脚本侧与 UI 侧各维护一份校验逻辑。

### 9.1 UI 审计链路

```
RagMaintenancePanel.refresh()
  -> task_states(self._root)                                  [10 个任务状态计算]
     -> [遍历 TASK_DEFS] 源 mtime / 输出 mtime 对比
        -> [缺源] status="缺源"
        -> [无输出或输出 mtime=0] status="待重建"
        -> [源 mtime > 输出 mtime + 1s] status="待重建"
        -> [否则] status="最新"
     -> _output_count(root/data/rag_corpus/outputs[0])        [(mtime, size) 缓存，未变不重复解析]
  -> list_pending(root/data/rag_corpus)                        [同一轮先算待精化清单]
     -> scan_blocks() -> ["pending"]
  -> self._refine_button.setText(f"索引精化（{n}）" / "索引精化 ✓")
  -> audit_summary(self._root, pending)                        [传入已算清单，避免重复读语料]
  -> _workspace.set_task_states(rows)                          [左栏状态点刷新]
  -> _refresh_status_summary(rows, issues)                     [操作栏状态摘要]
  -> _refresh_audit_banner(issues)                             [最多 3 条 + 折叠剩余]
```

### 9.2 audit_summary 校验项

```
audit_service.audit_summary(root, pending_refinement=None)
  -> heroes = json.loads(data/heroes.json) -> hero_names
     -> [读取失败] heroes_load_failed=True + AuditIssue(heroes_source_unavailable)   [防误报 orphan]
  -> collect_unclassified(hero_names, classification) -> AuditIssue(unclassified_hero)
     -> AuditIssue(target_tab="武将分类维护", target=未归类名列表)
  -> collect_orphan_category_keys(hero_names, classification) -> AuditIssue(orphan_category_key)
     [反向校验：分类表引用了 heroes.json 中不存在的武将]
  -> specials = json.loads(data/special_cards.json)
  -> collect_unknown_heroes(specials, hero_names) -> AuditIssue(unknown_hero)
     [泛指名/括号注释跳过；GENERIC_HERO_NAMES = {通用, —, 众多武将}]
  -> collect_missing_settlements(specials) -> AuditIssue(missing_settlement)
     [专属牌/专属战法牌缺结算详情；"死士"为非实体牌标记豁免]
  -> collect_card_points(payload) -> AuditIssue(card_points_structure/total/bad_suit/bad_point)
     [花色列、点数列、张数合计=162 校验；常量来自 card_points_repository 单一事实源]
  -> collect_equip_attrs(equips) -> AuditIssue(equip_attrs_structure/count/bad_equip_attrs)
     [件数=26、细分类型、距离修正校验；常量来自 equip_attrs_repository]
  -> [pending_refinement 非空] issues.insert(0, AuditIssue(pending_refinement))   [始终插入首位]
  -> collect_timeline_risk_messages(root) -> AuditIssue(timeline_risk)
     -> stale_overrides(timeline) -> TRIGGER_OVERRIDES 失效风险
     -> [hero.last_updated < hero_last_change] heroes.json 疑未同步
```

### 9.3 审计跳转

```
RagMaintenancePanel._jump_to_issue(issue)
  -> [issue.kind == "pending_refinement"] _open_refinement() -> return
  -> [无 target_tab] return
  -> key = issue.target_tab.removesuffix("维护")                [「武将分类维护」->「武将分类」]
  -> _workspace.select_source(key)
  -> [unclassified_hero] _classification.focus_unclassified()
  -> [unknown_hero / missing_settlement 且有 target] _special_cards.focus_item(*issue.target)
```

按钮文案映射（其余类型统一「去检查」）：`unclassified_hero`→「去归类」、`missing_settlement`→「去补全」。

### 9.4 脚本侧审计

```
maintain_rag.main()（构建前门禁）
  -> rag_audit.audit_hero_coverage(ROOT)
     -> json.load(heroes / hero_classification / special_cards / cards)
     -> collect_unclassified() -> collect_orphan_category_keys() -> collect_unknown_heroes()
     -> 疑似牌名启发式提取（结尾字 + 黑名单 + 已知名称覆盖区间 + 重叠匹配）
     -> collect_card_points() / collect_equip_attrs() / collect_missing_settlements()
  -> rag_audit.audit_version_timeline(ROOT)
     -> [无 mjs_adjustments.json] 提示未初始化
     -> stale_overrides(timeline) -> 失效风险
     -> hero_last_change 对比 last_updated -> 疑未同步
     -> [遍历语料目录] is_current=='false' 块统计 -> 语料过时块
  -> [issues 非空] 打印人工补充清单
  -> [--strict-audit] sys.exit(1)
```

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `task_states(root)` | `ui/maintenance/rag_maintenance_panel.py` | 10 个语料任务状态计算 | `RagMaintenancePanel.refresh()`, 测试 |
| `_output_count(path)` | 同上 | 语料块数读取（(mtime, size) 缓存） | `task_states()` |
| `audit_summary(root, pending_refinement)` | `business/rag/audit_service.py` | 结构化审计（`AuditIssue` 列表） | `RagMaintenancePanel.refresh()` |
| `collect_card_points(payload)` | 同上 | 卡牌点数源校验 | `audit_summary()`, `scripts/rag_audit.py` |
| `collect_equip_attrs(equips)` | 同上 | 装备属性源校验 | 同上 |
| `collect_missing_settlements(specials)` | 同上 | 专属牌缺结算详情 | 同上 |
| `collect_unclassified()` / `collect_orphan_category_keys()` | 同上 | 武将归类正反向校验 | 同上 |
| `collect_unknown_heroes(specials, hero_names)` | 同上 | 专属牌引用未知武将 | 同上 |
| `collect_timeline_risk_messages(root)` | 同上 | 时间轴风险摘要 | `audit_summary()` |
| `format_audit_issues(issues)` | 同上 | `AuditIssue` 转纯文本列表 | 兼容旧消费方/测试 |
| `audit_hero_coverage(root)` | `scripts/rag_audit.py` | CLI 版人工补充清单 | `maintain_rag.main()` |
| `audit_version_timeline(root)` | 同上 | 时间轴一致性审计 | `maintain_rag.main()`, `scripts/rag_audit.py __main__` |
| `load_hero_briefs(root, fallback_names)` | `business/rag/hero_brief.py` | 武将名/定位/技能文本（技能文本格式归位业务层） | `RagMaintenancePanel._load_heroes()` |

---

## 十、知识库维护工作台外壳

工作台采用「左栏导航 + 右侧 QStackedWidget + 底部折叠日志」的三段布局。左栏 10 项分两组：上组 5 个可编辑维护对象（每项挂真实面板实例，点击切右侧）、下组 5 个只读语料（仅状态展示，点击弹元信息框）。所有 QProcess 输出汇入模块底部的单一日志出口。

```
MainWindow._setup_ui()
  -> RagMaintenancePanel(root, hero_names)
     -> ScriptRunner(self) -> output.connect(_append_log) / finished.connect(_on_finished)
     -> load_hero_briefs(root, fallback)                       [business/rag/hero_brief]
     -> _setup_ui(): PageActionBar（刷新状态 / 索引精化 | 重建全部语料 / 重建语料+索引）
        + 审计横幅 + MaintenanceWorkspace
     -> _setup_sources()
        -> RuleDocPanel(root) + SpecialCardsPanel + CardPointsPanel + EquipAttrsPanel + HeroClassificationPanel
        -> [各面板] data_changed.connect(_on_child_changed)
        -> [RuleDocPanel] script_started/output/finished.connect 三个转发槽    [元规则脚本输出汇入单一日志]
        -> workspace.add_group("维护对象", 5) -> add_source(key, task_name, panel) x5
        -> workspace.add_group("只读语料", 5) -> add_source(key, task_name) x5
     -> refresh()

MaintenanceWorkspace（维护工作台布局外壳）
  -> MaintenanceSourceNav(self)
     -> source_selected / rebuild_requested / meta_requested 信号转发
  -> QStackedWidget（面板实例复用切换，保留各面板内部选中与滚动位置）
  -> 底部 QFrame 日志区（默认折叠 32px，构建时自动展开 180px）

左栏单行 _NavItem
  -> 状态点（8x8）+ 名称 + 状态词 + 「↻」单项重建按钮（仅待重建时可见）
  -> set_status(status)：最新=neutral / 待重建=warning / 缺源=danger
  -> mouseReleaseEvent：整行左键点击发 clicked（子控件自行消费不触发行切换）
  -> [带 widget] clicked -> MaintenanceSourceNav.select(key) -> source_selected -> select_source(key)
  -> [无 widget] clicked -> meta_requested.emit(key) -> _show_corpus_meta(key)
  -> rebuild_button.clicked -> rebuild_requested.emit(task_name)
     -> RagMaintenancePanel._run(["--force", "--only", task_name])

底部日志折叠态
  -> on_log_output(text)：未展开时累计行数 + 亮出 StatusBadge「N 行新输出」(warning 色调)
  -> expand_log() / collapse_log() / reset_unread() / set_log_meta("退出码 N · 耗时")

_refresh_audit_banner(issues)
  -> [无 issues] hide -> return
  -> set_tone(TONE_WARNING) + 标题「人工维护提示」
  -> [每条前 3 条] _build_audit_row(issue) -> _audit_list_layout.addWidget
     -> [有 target_tab] QPushButton(去检查/去归类/去补全) -> _jump_to_issue
  -> [超出] QLabel「还有 N 条提示，处理后点击「刷新状态」查看全部」

_set_busy(busy)                                                [执行期间]
  -> 禁用 刷新状态 / 重建全部语料 / 重建语料+索引 / 索引精化
  -> _workspace.set_interactive(not busy)                       [禁用左栏切换与单项重建]
```

| 函数 | 所在文件 | 作用 | 调用方 |
|------|----------|------|--------|
| `RagMaintenancePanel.refresh()` | `ui/maintenance/rag_maintenance_panel.py` | 语料状态 + 待精化 + 审计汇总刷新 | 面板按钮、子面板保存后、脚本结束后 |
| `RagMaintenancePanel._run(args)` | 同上 | ScriptRunner 执行 `maintain_rag.py` | 重建按钮、左栏 ↻ |
| `RagMaintenancePanel._jump_to_issue(issue)` | 同上 | 按审计条目跳转并定位 | 审计横幅按钮 |
| `RagMaintenancePanel._open_refinement()` | 同上 | 打开索引精化对话框 | 索引精化按钮、审计跳转 |
| `RagMaintenancePanel._show_corpus_meta(key)` | 同上 | 只读语料元信息弹窗 | 左栏只读项点击 |
| `MaintenanceWorkspace.add_source(key, task_name, widget)` | `ui/maintenance/maintenance_workspace.py` | 登记维护对象或只读语料项 | `RagMaintenancePanel._setup_sources()` |
| `MaintenanceWorkspace.select_source(key)` | 同上 | 切换右侧工作区（实例复用） | 左栏信号、`_jump_to_issue()` |
| `MaintenanceWorkspace.on_log_output(text)` | 同上 | 折叠态累计未读行数并亮角标 | 三个脚本输出槽 |
| `MaintenanceSourceNav.set_task_states(states)` | 同上 | 按任务名刷新各左栏项状态 | `RagMaintenancePanel.refresh()` |
| `_NavItem.set_status(status)` | 同上 | 状态点色调与「↻」按钮显隐 | `set_task_states()` |
| `ScriptRunner.run(program, cwd, args, root)` | `business/common/script_runner.py` | QProcess 异步执行 Python 脚本（同一时刻仅一个任务） | 三个维护面板 |
| `ScriptRunner.output` / `finished` 信号 | 同上 | 输出字节流与退出码 | 面板日志槽 |

---

## 十一、函数清单总表

各章节内已给出完整函数表，此处仅汇总跨章核心入口与调用方向，便于定位起点。

### RAG 底层（src/rag/）

| 函数 | 调用方 | 被调用方 |
|------|--------|----------|
| `build_index(rebuild)` | `maintain_rag.py`、`indexer.__main__`、`ai_batch --rebuild-rag-index` | `load_all_blocks()`, `SentenceTransformer`, `chromadb.PersistentClient` |
| `load_all_blocks()` | `build_index()`, `Retriever.blocks` | 12 个 `_norm_*()`, `json.loads` |
| `Retriever.search(query, heroes, top_k)` | `rag_prompt.build_*_rag_context()`, `retriever.__main__` | `build_search_where()`, `_vector_search()`, `_keyword_hits()`, `_apply_kind_quota()` |
| `Retriever.hero_blocks(hero)` | `rag_prompt.build_*_rag_context()` | 内存 `_hero_index` 倒排 |

### 语料构建（src/scripts/）

| 函数 | 调用方 | 被调用方 |
|------|--------|----------|
| `maintain_rag.main()` | QProcess 子进程 | `rag_audit.*`, `audit_rule_doc.audit()`, `task_changed()`, `run_script()`, `verify_outputs()` |
| `run_script(script_name, timeout)` | `maintain_rag.main()` | `subprocess.run(['-m', 'src.scripts.' + module])` |
| `verify_outputs(task)` | `maintain_rag.main()` | `audit_rule_doc.snapshot_counts()` |
| `rag_common.load_json()` / `save_json()` | 全部 build 脚本 | `atomic_write_json()` |
| `rag_curated.merge_curated(blocks, old_json_path)` | `build_rag_corpus.py`, `build_card_corpus.py` | `json.loads` |
| `build_rule_corpus.parse_rule_doc(doc_path)` | `build_rule_corpus.py`, `audit_rule_doc.audit()` | `HEADING_RE`, `SEPARATOR_RE` |

### 索引精化（src/business/rag/）

| 函数 | 调用方 | 被调用方 |
|------|--------|----------|
| `scan_blocks(corpus_dir)` | `RefinementSession.__init__()`, `list_pending/curated/normal()` | `_to_block()`, `json.loads` |
| `suggest_one(block, generator)` | `SuggestWorker.run()`, `generate_suggestions()` | `generator.complete()`, `extract_json()`, `_to_update()` |
| `apply_curated(corpus_dir, updates, fname)` | `RefinementSession.apply_updates()` | `_atomic_json_write()` |
| `build_generator(profile_name)` | `IndexRefinementDialog._generator()`, `propose_rule_changes.py` | `resolve_api_config()`, `AIBatchGenerator` |
| `RefinementSession.collect_update(block_id, texts)` | `IndexRefinementDialog._collect_update()` | 双基线比对 |
| `RefinementSession.apply_updates(updates_by_file)` | `_save_current()`, `_save_all()` | `apply_curated()`, `sync_saved()` |
| `SuggestController.start(blocks, generator, single)` | `_suggest_current()`, `_suggest_all()` | `SuggestWorker.start()` |
| `SuggestController.cancel_and_shutdown()` | `IndexRefinementDialog.reject()` | `generator.cancel()`, `generator.close()`, `worker.wait()` |
| `SuggestWorker.run()` | Qt 线程 | `suggest_one()`, `result_ready.emit()` |

### 元规则维护与数据源维护

| 函数 | 调用方 | 被调用方 |
|------|--------|----------|
| `rds.parse_audit_output(text)` | `RuleDocPanel._on_audit_finished()` | 正则解析 |
| `rds.parse_sync_diff(path)` | `RuleDocPanel._on_sync_finished()` | `json.loads` |
| `rds.update_proposal_item(...)` | `ProposalItemConfirmDialog` 确认 | `atomic_write_json()` |
| `rds.pending_to_proposal(root, pending_id)` | `RuleDocPanel._to_proposal()` | `load_pending()`, `atomic_write_json()` |
| `rds.build_faq_revise_row(line, text)` | UI 差异预览、`apply_rule_proposal.py` | 表格单元格替换 |
| `rule_doc_ops_service.validate_confirmed_row(diff, new)` | `RuleDocPanel._collect_confirmed_rows()` | 三规则校验 |
| `rule_doc_ops_service.save_confirmed_diffs(root, rows)` | `RuleDocPanel._apply_diffs()` | `atomic_write_json()` |
| `audit_rule_doc.audit(...)` | `audit_rule_doc.main()`, `maintain_rag.main()`, `apply_rule_proposal.run_audit_strict()` | `parse_rule_doc()`, `sync_rule_stats.diff_sections()` |
| `sync_rule_stats.diff_sections(doc_text, data, only)` | `sync_rule_stats.main()`, `audit_rule_doc.audit()` | `find_section()`, `table_rows_in()`, `gen_*_rows()` |
| `sync_rule_stats.apply_confirmed(confirmed, doc_text)` | `sync_rule_stats.main() --apply-json` | 预检 + `_atomic_write_text()`, `append_changelog()`, `refresh_snapshot()` |
| `apply_rule_proposal.apply_proposal(doc_text, proposal)` | `apply_rule_proposal.main()` | `APPLYERS` 五种合入动作 |
| `CardPointsService.add_card/replace_card/delete_card` | `CardPointsPanel._add/edit/delete_card()` | `CardPointsRepository.*` |
| `ClassificationService.save/set_hero_categories/...` | `HeroClassificationPanel` | `HeroClassificationRepository.*` |
| `suggest_hero_categories(...)` | `HeroClassificationPanel._HeroCategoryWorker` | `generator.complete()`, `extract_json()` |
| `atomic_write_json(path, data, indent)` | 全部仓储、`refinement_service`、`rule_doc_service`、`rag_common.save_json` | `tempfile.mkstemp`, `os.replace` |
| `JsonRepository._save_or_rollback(snapshot)` | 四个仓储 CRUD | `save()`, `_restore()` |

---

## 十二、外部调用关系总览

### 12.1 本模块被外部调用

```
src.business.fetching.guide_fetch_service / synergy_fetch_service
  -> QProcess.start(["-m", "src.scraper.ai_batch", "--guide" | "--synergy-*", ...])
     [经典模式 use_rag=False] 追加 --no-rag
       ─── 子进程内部 -> rag_prompt -> retriever（本模块）

src.scraper.ai.generation
  -> prompt_utils.build_guide_prompt() / build_synergy_prompt()
     -> rag_prompt.build_rag_context() / build_synergy_rag_context()   [本模块]

src.scraper.ai.batch (main)
  -> [--rebuild-rag-index] rag.indexer.build_index(rebuild=True)
```

### 12.2 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| `src.data.json_repository.atomic_write_json()` | 全部写路径的原子写实现 |
| `src.data.card_points_repository` / `equip_attrs_repository` | 审计校验常量（花色/点数/张数/件数/细分类型/距离修正）单一事实源 |
| `src.data.card_points_repository.CardPointsRepository` 等四个仓储 | 维护面板底层数据源 |
| `src.data.hero_timeline` | 武将变更时间轴（`stamp_hero_block`、`stale_overrides`、`hero_last_change`、`CORPUS_BASE_DATE`） |
| `src.data.hero_classification_repository` / `special_cards_repository` | 分类与专属牌仓储 |
| `src.data.combo_manager.ComboManager` | 实战配队（归 module_peak_combos） |
| `src.scraper.ai.api_generator.AIBatchGenerator` | 索引精化与分类建议的 LLM 生成器 |
| `src.scraper.ai.json_extract.extract_json` | LLM 输出 JSON 提取 |
| `src.scraper.ai.rag_prompt` | RAG 注入 prompt（归 module_ai_batch，本模块提供检索底座） |
| `src.scraper.ai.prompt_utils` / `rule_summary` | 提示词拼装与核心规则兜底（归 module_ai_batch） |
| `src.business.common.script_runner.ScriptRunner` | QProcess 公共封装（经 `src/ui/shared/widgets.py` 再导出） |
| `src.ui.shared.persist.run_edit_dialog` | 模态编辑 + 保存失败重试循环 |
| `src.ui.shared.rich_diff` | 提案/差异详情对话框的 Git 风格差异渲染 |
| `src.ui.library.hero_classification_panel` / `special_cards_panel` | 物理位于 `src/ui/library/`，由本模块工作台承载 |
| `src.config.env` | `PROJECT_ROOT`、`parse_env_file()`、`resolve_api_config()`、`PROVIDER_PRESETS` |
| `sentence_transformers.SentenceTransformer` | bge-small-zh-v1.5 向量模型 |
| `chromadb.PersistentClient` | 本地向量数据库 |
| `docs/元规则整理-完整版.md` | T0 母本文档 |
| `data/rag_corpus/`、`data/rag_index/chroma/` | 语料与向量索引落盘目录 |

### 12.3 模块归属边界

| 路径 | 归属模块 |
|------|----------|
| `src/data/*_repository.py` | module_data |
| `src/scraper/ai/rag_prompt.py` / `prompt_utils.py` / `rule_summary.py` | module_ai_batch |
| `src/business/maintenance/combo_import_service.py` | module_peak_combos |
| `src/business/maintenance/data_management_service.py` | module_business |
| `src/business/common/script_runner.py` | 公共基础设施（本模块经 `src/ui/shared/widgets.py` 再导出使用） |

---

## 待确认清单

| # | 待确认项 | 当前处理 |
|---|----------|----------|
| 1 | `rag_common.py` 文件头注释写"统一 8 个 build 脚本"，但 `task_defs.TASKS` 实际有 10 个任务、`src/scripts/` 下有 10 个 `build_*` 脚本 | 以代码为准按 10 个统计，注释视为历史遗留未同步 |
| 2 | `call_graph_business.md` 第 10.1 节与 `call_graph_ui.md` 第 10.1 节写"遍历 8 个语料任务 TASK_DEFS" | 两份文档基线为 2026-07-22 / 2026-09-04，早于 2 个新任务（武将分类、组合、攻略）的口径；本文档按当前代码写 10 个 |
| 3 | `SpecialCardsPanel`、`HeroClassificationPanel` 的完整内部方法清单未逐行复读 | 本文档只覆盖与本模块相关的写路径与 `data_changed` 联动；物理位置已确认为 `src/ui/library/` |
| 4 | `rag_prompt.py` 后半段（`_format_rag_chunks`、`build_synergy_rag_context` 内部细节）未逐行复读 | 相关描述引自 `call_graph_ai_batch.md`（同基线） |
| 5 | `indexer.py` 中 `RAG_PROJECT_DIR` 常量是否仍被使用 | `config.py` 中定义但本模块未见消费点，未列入调用链 |
| 6 | 语料块数期望值（如武将 615 / 卡牌 49 / 特殊机制 83 / 装备 27）会随源数据变化 | 数值取自 `task_defs.py` 当前提交，属易变事实 |
