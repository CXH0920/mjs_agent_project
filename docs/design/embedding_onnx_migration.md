# RAG 编码器 ONNX 化设计记录

> 实施于 1.2.1 基线（2026-10-11），方案 v2 定稿。本文记录六条已定决策及其依据，
> 供后续维护者理解决策约束，改动前先读本文。

## 背景与目标

RAG 检索链路原先依赖 sentence-transformers + torch（540MB），且仅完整版可用 RAG。
本次把编码器推理切换到 ONNX Runtime + tokenizers（运行期约 10MB 依赖），
达成：精简版自带 RAG、完整版显著变小、索引体积回落、检索行为可观测。

## 决策记录

### D1 只换编码器，保留 Chroma/HNSW

不动存储层（`mjs_rag_v1` 集合、cosine 空间、查询前缀、RRF 融合、`_vector_search`
契约）。编码器语义等价由对齐门禁保证后，存储层的向量空间无需迁移。
改存储层的收益（换库/换索引结构）与本次目标无关，不引入。

### D2 ONNX 产物与索引都不进私有仓，用三件套替代仓库保障

`pull_data`/`PRIVATE_PATHS`/manifest 零改动。产物不进任何仓库的理由：向量索引
完全可由语料重建（纯派生资产），ONNX 模型可由源权重重新导出；进仓会把 100MB+
的派生二进制塞进同步链。替代保障三件套：

1. **一条命令重建**：`export_embedding_onnx` → `pytest tests/test_rag_onnx_parity.py`
   → `python -m src.rag.indexer`（缺源权重时自动经 HF 镜像下载）；
2. **本地冷备份**：只备份 `data/rag_models/onnx/`（约 92MB 含侧车），索引不备份，
   模型重新导出时才更新，落项目外路径（季度项，见周更操作手册）；
3. **发版断言**：release.py 的 ①ONNX 就位检查（缺失自动导出）→ ②打包前按 onnx
   backend 强制重建索引并断言块数==语料块数 → ③包内三件套哈希校验 →
   ④包内 `eval_rule_faqs --limit 3` 检索门禁。

### D3 对齐门槛先测量后固化（fp32 ≥0.999 / top30 ≥29/30）

`tests/test_rag_onnx_parity.py` 两趟工作流：第一趟 `MJS_PARITY_RECORD=1` 只打印
观测分布；第二趟按 `门槛 = min(观测) − 余量`（不低于 0.999）写死常量后正常判定。
**门槛只随观测值调整，不允许手改让灯变绿。**

实测固化依据（2026-10-11，torch 2.12 / onnxruntime 1.23 / opset 18）：
50 块 + 20 query 逐条余弦 fp32 min=1.000000，top-30 重合 30/30 → 固化 0.999 与 29。
两处曾拉低对齐的坑（均已修复在导出脚本）：
- `sentence_bert_config.json` 的 `do_lower_case=true` 高于 `tokenizer_config.json`
  的 false——ST 运行时做小写化（`FAQ`→`faq`），tokenizer.json 必须按 ST 同款
  配置重新序列化，不能直接复制源文件；
- transformers 在 `do_lower_case=True` 时默认连带 `strip_accents=True`（NFD+剥
  变音符，`≠` 被拆成 `=`），须显式 `strip_accents=False` 才与 ST 一致。

导出工具链坑：torch 2.12 dynamo 导出（1）校准样本只有单条时 batch/seq 维会被
错误专门化，运行期 Add 节点广播崩溃——校准样本须为**不等长多条**；（2）权重默认
落 `model.onnx.data` 外部文件——导出后内联回单文件，保住三件套契约。

### D4 打包维持双包形态，只把 RAG 资产加进精简版

`--full` 结构、维护页归属（`.full_build`）、`is_full_build()` 分叉都不动。两个
自然结果：
- ONNX 后两个包都不再需要 torch/transformers，`--full` 不再 un-exclude 这三项，
  完整版因此下降 500MB 以上；
- 精简版有完整 RAG 检索但**没有知识库维护页**（该页仍由 `.full_build` 控制），
  重建索引走 CLI：`python -m src.scripts.maintain_rag --force --build-index`。

### D5 S7 裁决：维持 fp32 打包，不切 int8

int8 对齐实测 min 余弦 ≈0.952、top-30 重合 min 27/30，低于打包标准（≥0.99 /
≥29/30），量化损失明显。裁决不打包 int8（spec 收集时排除 `model.int8.onnx`）；
对齐测试保留 int8 信息性下限（0.90 / 25）看守量化管道不整体崩坏，不作发版门禁。

### D6 编码器抽象与兼容不变量

`src/rag/embedder.py` 统一 `encode()` 契约，onnx（默认）/ st（回滚档，
`RAG_EMBEDDING_BACKEND=st`）经 `get_embedder()` 单例选择。不变量：
- `encode()` 返回 `np.ndarray`——indexer 切片 `.tolist()`、retriever `q.tolist()`
  依赖 ndarray 行为；
- 单字符串输入返回一维 `(D,)`（与 ST 一致，Chroma 拒收嵌套 embedding）；
- onnxruntime / tokenizers 在函数内惰性导入，CI 与无 torch 环境可导入模块；
- ONNX 侧车（`sha256.json`）记录文件哈希与 graph `input_names`，喂入输入名以
  侧车为准（`token_type_ids` 有/无不硬编码），构造即自校验、缺件熔断并给
  `python -m src.scripts.export_embedding_onnx` 修复指引。

## 索引治理（S4 附带决策）

重建从「delete_collection」改为「整目录 rmtree + 新建」：历次 delete_collection
留下 21 组孤立段目录（80.9MB 里绝大部分是残留），索引反正可由语料整建。
保留「删不掉就中止、不静默继续」哲学。doctor 新增 RAG索引 检查组（体积 ≤25MB
PASS、段目录数 ==1、ONNX 三件套哈希）——不打开 chroma 数据库，「索引块数==语料
块数」深校验放发版流程（那里本来就要打开索引）。
