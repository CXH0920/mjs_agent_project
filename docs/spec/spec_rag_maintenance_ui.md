# 知识库维护工作台规格（RAG 本地化维护）

> 状态：已实施
> 范围：主导航「知识库维护」页面；武将/卡牌/专属牌等数据变更后，在应用内本地重建 RAG 语料与向量索引。
> 不再依赖外部 mjs_rag_project 仓库：数据源、文档源、构建脚本、嵌入模型与向量索引均已收编到 test_project。

## 一、目标

- 武将新增/调整、卡牌注解、专属牌维护后，能直观看到哪些 RAG 语料任务已过期（待重建）。
- 一键在本地执行「重建语料 → 重建向量索引」，实时展示日志与块数校验，无需手工跑命令行或跨仓库同步。

## 二、UI 位置

- 主导航第 4 页「知识库维护」（`src/ui/maintenance/rag_maintenance_panel.py`，`MainWindow.PAGE_CONTEXTS` 与 `NavigationRail.PAGE_LABELS` 同步追加）。
- 页面为多页签结构（复用 `librarySectionTabs` 样式），五个页签：
  1. **语料状态**：PageActionBar（状态文字 + 刷新状态 / 重建武将语料 / 重建全部语料 / 重建语料+索引）+ 任务状态表（ragTableSurface 卡片，8 个语料任务 × 状态/输出块数/数据源）+ 人工维护 NoticeBanner（未归类武将、专属牌引用未知武将、点数/装备/结算回填校验等 warning；无问题时显示成功）+ 执行日志区（ragLogSurface 卡片内只读等宽 QPlainTextEdit，QProcess 实时追加）；
  2. **专属牌维护**：复用 `src/ui/library/special_cards_panel.py` 的 `SpecialCardsPanel`（从资料库移入，数据源 `data/special_cards.json`；专属牌/专属战法牌条目含牌面事实字段：花色/点数/攻击范围/结算详情，由原 xlsx【专属牌】sheet 迁移回填）；
  3. **卡牌点数维护**：`src/ui/maintenance/card_points_panel.py`，维护 `data/card_points.json`（原 xlsx sheet1 + 硬编码判定规则迁移）——牌面明细 162 张（只读浏览 + 新增/编辑/删除牌行）、卜卦判定规则（12 条，增删改）；「从 xlsx 导入」按钮调用 `scripts/migrate_excel_to_json.py --only points` 基于归档 xlsx 全量覆盖点数数据；
  4. **装备属性维护**：`src/ui/maintenance/equip_attrs_panel.py`，维护 `data/equip_attrs.json`（原 xlsx sheet2 与 build_equip_attr.py 硬编码迁移）——26 件装备表格编辑（名称/备注只读，细分类型/攻击范围/距离修正可编辑，保存时校验）；
  5. **武将分类维护**：`src/ui/library/hero_classification_panel.py`，维护 `data/hero_classification.json` 的三个子页签——分类管理（分类 CRUD，删除时清理归类/克制链引用）、克制链（分类 → 克制说明文本）、武将归类（全部/未归类/已归类筛选 + 分类多选 + 未归类定位）。
- 子面板保存后发 `data_changed`，知识库维护页自动刷新「语料状态」并标记待重建。
- 武将分类数据约束：分类 `name` 唯一；`hero_categories` 与 `counter_chain` 引用的分类必须存在；`updated_at` 保存时自动刷新为当天。

## 三、数据流与本地管线

- 权威源：`data/heroes.json`、`data/cards.json`、`data/card_annotations.json`、`data/special_cards.json`、`data/hero_classification.json`、`data/card_points.json`、`data/equip_attrs.json`、`docs/元规则整理-完整版.md`（原 `data/mjs卡牌点数.xlsx` 已归档至 `data/archive/`，仅由迁移脚本作为重新导入通道）。
- 语料产物：`data/rag_corpus/*.json`（build 脚本直接输出到此目录）；向量索引：`data/rag_index/chroma`。
- 执行命令：`python scripts/maintain_rag.py --force [--only 武将] [--build-index]`（工作目录为项目根）。
- 任务状态判定：任一源文件 mtime 晚于对应语料输出 → 待重建；输出缺失 → 待重建；源缺失 → 缺源。
- 审计：未归类武将（heroes 未出现在 hero_classification.hero_categories）；专属牌 hero 不在武将库且非「通用/—/众多武将/以“等”结尾」（按顿号/逗号拆分后逐项校验）；技能描述中的疑似牌名/道具名（启发式提取 + 黑名单/已知名称/排除清单过滤，非专属牌，仅作人工确认提示）；card_points.json 花色（♥♣♠♦太极）/点数（1~8）/张数（162）合法性；equip_attrs.json 件数（26）/细分类型/距离修正合法性；专属牌/战法牌结算详情回填完整性（死士为非实体牌标记，豁免）。

## 四、验收标准

- 页面显示 8 个语料任务与真实块数；数据变更后「待重建」标记正确出现。
- 点击「重建武将语料」仅重建武将/武将分类语料；「重建全部语料」重建 8 个任务；「重建语料+索引」额外重建向量索引。
- 执行期间按钮禁用，日志实时滚动；结束后自动刷新任务状态。
- 全部过程不访问 `G:\py_savepoint\mjs_rag_project`（嵌入模型缓存 `data/rag_models/` 为本地副本）。

## 五、索引精化（2026-08 新增）

- 入口：知识库维护页 → 「索引精化」按钮，打开 IndexRefinementDialog。
- 维护对象：卡牌RAG语料.json（20 块）与 武将RAG语料.json（123 技能块）中无 curated 且索引字段为空的块。
- 字段：timing / trigger_condition / keywords / related / target；支持 LLM 建议（DeepSeek）与人工填写。
- 写回：Apply_curated 更新块顶层索引字段并写入 curated（method=llm/manual、updated_at），build 脚本重跑时通过 scripts/rag_curated.py 保留精化值。
