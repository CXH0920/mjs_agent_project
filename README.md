# 名将杀 Agent

名将杀桌面辅助工具，面向名将杀手游的轻度玩家，运行于 PC 端 MuMu 模拟器。

核心功能：**选将推荐** + **武将数据库查询**。

> 开发中，当前完成阶段：阶段一（基础建设）+ 阶段二（数据采集）+ 阶段三（桌面应用 UI）。

---

## 目录结构

`
test_project/
│
├── AGENTS.md              # 开发规范
├── PLANS.md               # 实施计划
├── environment.yml        # Conda 环境定义
├── README.md              # 本文件
│
├── src/                   # 源代码
│   ├── config/              # 配置管理（新增）
│   │   └── env.py               # .env 文件解析/加载/保存
│   ├── data/
│   │   ├── models.py          # 数据模型 (Pydantic)
│   │   ├── manager.py         # 统一入口 + 增量更新函数
│   │   ├── hero_manager.py    # 武将数据管理器
│   │   ├── synergy_manager.py # 相性评分数据管理器
│   │   └── guide_manager.py   # 攻略数据管理器
│   ├── capture/           # 采集层（待开发）
│   ├── business/          # 业务层（待开发）
│   ├── ui/                # UI 层（已完成）
│   └── scraper/           # 数据采集
    │       ├── crawler.py         # 爬虫核心模块（公开 API）
│       ├── official.py        # 官网爬虫
│       ├── incremental.py     # 增量/指定爬虫
│       ├── ai_batch.py        # AI 批量生成主入口（共享基础设施）
│       ├── ai_guide.py        # 攻略生成流程（从 ai_batch 拆分）
│       └── ai_synergy.py      # 相性评分生成流程（从 ai_batch 拆分）
│
├── data/                  # 本地 JSON 数据
│   ├── heroes.json        # 武将基础数据（149 个武将）
│   ├── synergies.json     # 相性评分（6 条样本）
│   └── guides.json        # 武将攻略（4 份样本）
│
├── tests/
│   ├── test_models.py           # 数据模型单元测试（25 个用例）
│   ├── test_ai_batch.py         # AI 批量生成 + 配置加载测试（33 个用例）
│   ├── test_hero_manager.py     # HeroManager 单元测试（13 个用例）
│   ├── test_synergy_manager.py  # SynergyManager 单元测试（13 个用例）
│   ├── test_guide_manager.py    # GuideManager 单元测试（11 个用例）
│   ├── test_incremental_update.py # 增量更新集成测试（8 个用例）
│   └── test_ui.py               # 配置管理 UI 工具测试（4 个用例）
│
└── docs/
    ├── field_mapping.md      # 官网字段映射说明
    └── prompts/              # AI 生成 Prompt 模板
        ├── hero_guide.md     # 武将攻略 Prompt
        └── synergy_score.md  # 相性评分 Prompt
`

---

## 配置管理

### config.env 配置文件

项目根目录的 `config.env` 用于集中管理 API 配置和运行时参数：

```env
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_API_URL=https://api.deepseek.com/v1/chat/completions
DEEPSEEK_MODEL=deepseek-v4-pro
REQUESTS_PER_MINUTE=30
HTTP_TIMEOUT=300
MAX_RETRIES=3
```

### 配置模块（`src/config/`）

`src/config/env.py` 提供统一的配置加载/保存接口，被 `scraper/ai_batch.py` 和 `ui/settings_dialog.py` 共同使用：

- `parse_env_file()` — 解析 .env 文件
- `get_api_config()` — 获取 API 配置（config.env > 环境变量 > 默认值）
- `get_runtime_params()` — 获取运行时参数（速率限制、超时、重试次数）
- `save_env_file()` — 原子写入 .env 文件

---

## 快速开始

### 1. 环境准备

#### 1.1 创建 Conda 环境（首次）

```
conda env create -f environment.yml
conda activate myenv
```

#### 1.2 激活现有环境

```
conda activate myenv
```

#### 1.3 安装依赖

```
pip install pydantic httpx beautifulsoup4 opencv-python easyocr mss PySide6 pytest
```

> 本项目核心数据模型依赖 `pydantic`，运行测试依赖 `pytest`，其余为后续阶段的预装依赖。

### 2. 运行单元测试

```
pytest tests/ -v
```

预期输出：97 passed（25 数据模型 + 33 AI 批量生成 + 13 HeroManager + 13 SynergyManager + 11 GuideManager + 8 增量更新 + 4 UI 工具）

### 3. 数据采集（官网爬虫）

```
# 预览模式（不写入文件）
python -m src.scraper.official --dry-run

# 完整采集（写入 data/heroes.json）
python -m src.scraper.official

# 指定输出路径
python -m src.scraper.official --output data/my_heroes.json

# 开启详细日志
python -m src.scraper.official --verbose
```

> 需要网络连接访问 https://mjs.ztgame.com/baike/
> 每次运行会覆盖目标文件，建议先备份

### 3.5 增量采集（增量爬虫）

`Bash
# 增量模式：只爬取本地还没有的武将，追加写入 data/heroes.json
python -m src.scraper.incremental --incremental

# 预览增量结果（不写入文件）
python -m src.scraper.incremental --incremental --dry-run

# 按武将名称采集（支持模糊匹配，多个用逗号分隔）
python -m src.scraper.incremental --hero 诸葛亮
python -m src.scraper.incremental --hero 诸葛亮,关羽,张飞

# 按武将 ID 采集（多个用逗号分隔）
python -m src.scraper.incremental --hero-id 52,114,141

# 增量 + 指定名称组合（只处理本地没有的诸葛亮）
python -m src.scraper.incremental --incremental --hero 诸葛亮

# 指定输出路径
python -m src.scraper.incremental --hero 诸葛亮 --output data/my_heroes.json

# 启用详细日志
python -m src.scraper.incremental --hero 诸葛亮 --verbose
`

> 需要网络连接访问 https://mjs.ztgame.com/baike/
> 增量模式会自动去重，不会重复添加已存在的武将
> 指定名称/ID 模式支持 --dry-run 预览数据，确认无误后再移除该参数执行写入

### 5. AI 批量生成武将攻略和相性评分

使用 DeepSeek API 批量生成攻略和相性评分数据。

> **重构说明**：`ai_batch.py` 中的配置加载逻辑已抽取到 `src/config/env.py`，
> 攻略生成循环和相性评分生成循环已分别独立为 `src/scraper/ai_guide.py` 和 `src/scraper/ai_synergy.py`，
> `ai_batch.py` 作为共享基础设施和 CLI 入口保持不变。

> 前置条件：需要 DeepSeek 开放平台 API Key，并已通过官网爬虫采集了武将基础数据。
> 在项目根目录的 `config.env` 中配置 API Key（参见下方说明）。

```bash
# 预览模式（估算成本，不调用 API）
python -m src.scraper.ai_batch --dry-run --guide --synergy

# 仅生成攻略（149 个武将）
python -m src.scraper.ai_batch --guide

# 仅生成相性评分（11,026 对，耗时较长）
python -m src.scraper.ai_batch --synergy

# 同时生成攻略和相性
python -m src.scraper.ai_batch --guide --synergy

# 指定评分过滤下限
python -m src.scraper.ai_batch --guide --synergy --score-threshold 2

# 使用环境变量设置 API Key（备用方案）
$env:DEEPSEEK_API_KEY = "sk-xxx"
python -m src.scraper.ai_batch --guide --synergy
```

### config.env 配置说明

在项目根目录创建 `config.env` 文件，格式如下：

```env
# DeepSeek API 配置
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DEEPSEEK_API_URL=https://api.deepseek.com/v1/chat/completions
DEEPSEEK_MODEL=deepseek-v4-pro

# 运行时参数
REQUESTS_PER_MINUTE=30
HTTP_TIMEOUT=300
MAX_RETRIES=3
```

配置优先级：**config.env > 环境变量 > 默认值**

> 支持断点续传：中断后重新运行会跳过已生成的数据。
> 输出经过 Pydantic 模型校验，不合法的数据不会写入文件。
> 相性评分可通过 `--score-threshold` 过滤低分组合，减少无关数据。
> 所有配置项均可在 `config.env` 中设置，无需 CLI 参数。

### 6. 数据验证与查询

```
python -c "import sys; sys.path.insert(0, '.'); from src.data.manager import DataManager; dm = DataManager(); dm.load_all(); print(f'武将: {len(dm.list_heroes())} 个'); print(f'相性: {len(dm.list_synergies())} 条'); print(f'攻略: {len(dm.list_guides())} 份'); zg = dm.get_hero(114); print(f'ID=114: {zg.name} ({zg.faction}) - {zg.position}'); sg = dm.get_synergy(114, 141); print(f'{zg.name} <-> 关羽: 相性 {sg.score} 分 (评级 {sg.synergy_rating})'); wei = dm.list_heroes_by_faction('曹魏'); print(f'曹魏武将: {len(wei)} 个')"
```

### 6. 项目文件概览

```
src/data/models.py              # Pydantic 模型定义
src/config/env.py               # 配置管理：.env 解析/加载/保存
src/data/manager.py             # JSON 读写 + 查询 + 增量更新
src/scraper/official.py         # 官网爬虫（数据采集 + HTML 清洗 + Pydantic 校验）
src/data/manager.py             # JSON 读写 + 查询 + 增量更新
src/data/hero_manager.py        # 武将数据管理器
src/data/synergy_manager.py     # 相性评分数据管理器
src/data/guide_manager.py       # 攻略数据管理器
src/scraper/ai_guide.py         # 攻略生成流程（从 ai_batch 拆分）
src/scraper/ai_synergy.py       # 相性评分生成流程（从 ai_batch 拆分）
src/scraper/ai_batch.py         # AI 批量生成（DeepSeek API + 断点续传 + Pydantic 校验）
docs/prompts/hero_guide.md      # 攻略生成 Prompt 模板
docs/prompts/synergy_score.md   # 相性评分 Prompt 模板
data/heroes.json                # 149 个武将基础数据
data/synergies.json             # 6 条相性评分样本
data/guides.json                # 4 份武将攻略样本
tests/test_models.py            # 数据模型单元测试（25 个用例）
tests/test_ai_batch.py          # AI 批量生成单元测试（27 个用例）
```

### 7. 常见问题

| 问题 | 解决 |
|------|------|
| `conda`: 未找到命令 | 安装 Miniconda/Anaconda 并确保已加入 PATH |
| `ModuleNotFoundError: No module named src` | 在项目根目录下执行命令 |
| 爬虫网络超时 | 检查网络连接，重试（内置 3 次重试） |
| 爬虫 Pydantic 校验失败 | 官网数据格式可能已变更，查看日志中的异常数据 |


### 8. 数据处理说明

#### 8.1 官网技能描述清洗

官网爬取的数据中，`skill_desc` 字段为 HTML 格式，一个技能包含 4 个段落：

```
技能描述    → 技能核心效果描述    → 保留到 Skill.description
结算详情    → 规则结算细则        → 保留到 Skill.settlement
技能典故    → 历史典故出处        → 丢弃
设计思路    → 设计思路说明        → 丢弃
```

**清洗流程（先拆分后清洗）：**

1. 用正则 `<p>(?:<[^>]+>)*\s*<strong>段落标题</strong>...` 在原始 HTML 中定位段落标题
2. 按标题位置切分出各段落的原始 HTML 片段
3. 逐段剥离 HTML 标签、unescape HTML 实体、规范化空白
4. 只保留"技能描述"和"结算详情/结算详解"，其余丢弃

**结算段落标题的三种历史命名：**

官网技能描述中，结算部分使用的段落标题并非统一名称，存在三种历史变体：

| 命名 | 出现频次 | 示例技能 |
|------|---------|---------|
| `结算详情` | 大部分技能 | 吕蒙/白衣渡江 |
| `结算详解` | 部分技能 | 侯嬴/修身洁行 |
| `技能详解` 或 `技能详情` | 少量技能 | 郭隗/请自隗始、尉缭/挟义而战 |

三种命名均映射到 `Skill.settlement` 字段，按 `结算详情 → 结算详解 → 技能详解 → 技能详情` 优先级取非空值。

**边界情况处理：**

少数技能的 HTML 结构不完全规范。例如"郑国/疲秦之计"中用了 `<p><br /><strong>技能典故</strong></p>` 而非标准的 `<p><strong>技能典故</strong></p>`，需要在 `<p>` 和 `<strong>` 之间允许 `<br />` 等零散标签存在。当前正则为 `<p>(?:<[^>]+>)*\s*<strong>`，兼容此变体。

#### 8.2 官网数据局限

| 字段 | 说明 |
|------|------|
| `title`（称号） | 官网无此字段，默认为空 |
| `difficulty`（难度） | 官网无此字段，默认 MEDIUM(2) |
| `mode_viability`（模式强度） | 官网无此字段，需 AI 或人工评估 |


---

## 阶段一：项目脚手架与数据模型（已完成 ✅）

### 数据模型 (src/data/models.py)

| 模型 | 说明 | 关键字段 |
|------|------|----------|
| Hero | 武将基础数据 | id(int), name, title, faction, position, gender, max_hp, max_hand, skills, difficulty(1-5), mode_viability |
| Skill | 武将技能 | name, description(技能描述), settlement(结算详情) |
| SynergyScore | 武将间相性评分 | hero_a_id, hero_b_id, score(-10~10), synergy_rating(S/A/B/C/D), combo_ceiling, combo_stability, adaptability |
| HeroGuide | 武将攻略指南 | hero_id, key_points, counters(list[int]), synergizes_with(list[int]), description, tips_for_beginners |
| Card | 对局内基础卡牌 | id, name, card_type(行动牌/战法牌/装备牌), card_desc, card_detail, card_amount |
| IncrementalUpdate | 增量更新结构 | added/modified/removed 三类变更数据 |

**官网数据解析说明**：Hero、Card 模型通过 alidation_alias 支持中文字段名映射（如 角色ID、名称、体力上限），方便官网爬虫数据直接解析。

### 数据管理器（拆分后）

原 DataManager 已拆分为三个职责单一、可独立使用和测试的 Manager：

#### HeroManager (src/data/hero_manager.py)

负责武将数据的 CRUD 与 JSON 持久化。

核心方法：
- load() / save() — JSON 文件读写
- get_hero(id) / list_heroes() — 查询
- get_hero_by_name(name) / search_heroes(keyword) — 按名称/关键词搜索
- list_factions() — 获取所有势力列表
- add_hero() / update_hero() / delete_hero() — 增删改

#### SynergyManager (src/data/synergy_manager.py)

负责相性评分数据的 CRUD 与 JSON 持久化。相性评分双向一致（(A,B) 和 (B,A) 视为同一对）。

核心方法：
- load() / save() — JSON 文件读写
- get_synergy(a_id, b_id) — 查询（顺序无关）
- list_synergies_for_hero(id) — 查询某个武将的所有相性
- add_synergy() / update_synergy() / delete_synergy() — 增删改
- delete_synergies_for_hero(id) — 批量删除某个武将关联的所有相性

#### GuideManager (src/data/guide_manager.py)

负责攻略数据的 CRUD 与 JSON 持久化。

核心方法：
- load() / save() — JSON 文件读写
- get_guide(hero_id) / list_guides() — 查询
- add_guide() / update_guide() / delete_guide() — 增删改

#### 增量更新 (src/data/manager.py)

apply_incremental_update(hero_mgr, synergy_mgr, guide_mgr, update) 为独立函数，接收三个 Manager 实例和 IncrementalUpdate 模型，协调执行批量数据变更。

删除武将时会自动清理关联的相性和攻略数据。

### 样本数据

data/ 目录包含 **149 个武将**数据（含完整技能描述），6 条相性关系和 4 份攻略样本供开发和测试使用。武将 ID 为整数编号（1-149），相性和攻略中通过 int ID 引用武将。

### 单元测试

	tests/test_models.py 包含 **25 个测试用例**，覆盖：
- Skill 创建、空值校验
- Hero 创建、别名解析、默认值、边界校验
- SynergyScore 评分校验、评级枚举、取值范围
- HeroGuide int ID 引用
- Card 别名解析、CardType 枚举
- IncrementalUpdate 增量结构

	tests/test_hero_manager.py 包含 **13 个测试用例**，覆盖 HeroManager CRUD、查询方法和 JSON 持久化。
	
	tests/test_synergy_manager.py 包含 **13 个测试用例**，覆盖 SynergyManager CRUD、查询方法、批量删除和 JSON 持久化。
	
	tests/test_guide_manager.py 包含 **11 个测试用例**，覆盖 GuideManager CRUD、查询方法和 JSON 持久化。
	
	tests/test_incremental_update.py 包含 **8 个集成测试用例**，覆盖 apply_incremental_update 的各种操作组合和边界情况。
	
	tests/test_ui.py 包含 **4 个测试用例**，覆盖 .env 文件解析与原子写入。

---

## 后续阶段（计划）

| 阶段 | 内容 | 状态 |
|------|------|------|
| 二 | 数据采集工具（官网爬虫 2.1/2.2 + AI 批量生成 2.3-2.5，使用 deepseek-v4-pro 模型） | ✅ 已完成 |
| 三 | PySide 桌面应用（主窗口、武将浏览器、API 配置、全量/增量/指定获取、数据重载） | ✅ 已完成 |
| 四 | 屏幕采集（MuMu 截图、轮廓检测、OCR） | 待开发 |
| 五 | 推荐引擎（相性查询、推荐展示） | 待开发 |
| 六 | 文档与收尾 | 待开发 |

---

## 菜单栏功能说明

当前应用的菜单栏结构：

| 菜单 | 功能 | 说明 |
|---|---|---|
| 文件 > 退出 | Ctrl+Q | 关闭应用 |
| 工具 > API 配置 | | 编辑 config.env 配置文件 |
| 数据 > 重新加载数据 | F5 | 从磁盘重新读取 JSON 数据并刷新界面 |
| 数据 > 武将获取 > 全量获取 | | 确认后执行 `src/scraper/official.py`，从官网重新采集所有武将信息 |
| 数据 > 武将获取 > 增量获取 | | 确认后执行 `src/scraper/incremental.py --incremental`，仅爬取本地缺失的武将 |
| 数据 > 武将获取 > 指定获取 | | 弹窗提供势力筛选 + 名称模糊搜索 + 多选列表，确认后执行 `--hero-id` 模式增量爬虫 |
| 帮助 > 关于 | | 显示版本信息 |

> 三种获取模式均使用 QProcess 异步执行，不会阻塞 UI。
> **全量获取**：执行 src/scraper/official.py，重新采集所有武将。
> **增量获取**：执行 src/scraper/incremental.py --incremental，仅爬取本地缺失的武将。
> **指定获取**：弹窗提供势力复选框筛选 + 名称模糊搜索 + 多选武将列表，确认后执行 src/scraper/incremental.py --hero-id id1,id2,...。
> 采集完成后需通过 **数据 > 重新加载数据** 刷新界面。

---

## 开发规范

参见 AGENTS.md。
