# 名将杀 Agent

名将杀桌面辅助工具，面向名将杀手游的轻度玩家，运行于 PC 端 MuMu 模拟器。

核心功能：**选将推荐** + **武将数据库查询**。

> 开发中，当前完成阶段：阶段一（基础建设）+ 阶段二（数据采集 2.1-2.5）。

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
│   ├── data/
│   │   ├── models.py      # 数据模型 (Pydantic)
│   │   └── manager.py     # JSON 读写 + 增量更新
│   ├── capture/           # 采集层（待开发）
│   ├── business/          # 业务层（待开发）
│   ├── ui/                # UI 层（待开发）
│   └── scraper/           # 数据采集（official.py + ai_batch.py 已完成）
│
├── data/                  # 本地 JSON 数据
│   ├── heroes.json        # 武将基础数据（149 个武将）
│   ├── synergies.json     # 相性评分（6 条样本）
│   └── guides.json        # 武将攻略（4 份样本）
│
├── tests/
│   ├── test_models.py     # 数据模型单元测试（25 个用例）
│   └── test_ai_batch.py   # AI 批量生成单元测试（27 个用例）
│
└── docs/
    ├── field_mapping.md      # 官网字段映射说明
    └── prompts/              # AI 生成 Prompt 模板
        ├── hero_guide.md     # 武将攻略 Prompt
        └── synergy_score.md  # 相性评分 Prompt
`

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
pip install pydantic httpx beautifulsoup4 opencv-python easyocr mss PyQt6 pytest
```

> 本项目核心数据模型依赖 `pydantic`，运行测试依赖 `pytest`，其余为后续阶段的预装依赖。

### 2. 运行单元测试

```
pytest tests/ -v
```

预期输出：52 passed（25 数据模型 + 27 AI 批量生成）

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

### 4. AI 批量生成武将攻略和相性评分

使用 DeepSeek API 批量生成攻略和相性评分数据。

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

### 5. 数据验证与查询

```
python -c "import sys; sys.path.insert(0, '.'); from src.data.manager import DataManager; dm = DataManager(); dm.load_all(); print(f'武将: {len(dm.list_heroes())} 个'); print(f'相性: {len(dm.list_synergies())} 条'); print(f'攻略: {len(dm.list_guides())} 份'); zg = dm.get_hero(114); print(f'ID=114: {zg.name} ({zg.faction}) - {zg.position}'); sg = dm.get_synergy(114, 141); print(f'{zg.name} <-> 关羽: 相性 {sg.score} 分 (评级 {sg.synergy_rating})'); wei = dm.list_heroes_by_faction('曹魏'); print(f'曹魏武将: {len(wei)} 个')"
```

### 6. 项目文件概览

```
src/data/models.py              # Pydantic 模型定义
src/data/manager.py             # JSON 读写 + 查询 + 增量更新
src/scraper/official.py         # 官网爬虫（数据采集 + HTML 清洗 + Pydantic 校验）
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

---

## 阶段一：项目脚手架与数据模型（已完成 ✅）

### 数据模型 (src/data/models.py)

| 模型 | 说明 | 关键字段 |
|------|------|----------|
| Hero | 武将基础数据 | id(int), name, title, faction, position, gender, max_hp, max_hand, skills, difficulty(1-5), mode_viability |
| Skill | 武将技能 | name, description(技能描述), settlement(结算详情) |
| SynergyScore | 武将间相性评分 | hero_a_id, hero_b_id, score(-10~10), synergy_rating(S/A/B/C/D), combo_ceiling, combo_stability, adaptability |
| HeroGuide | 武将攻略指南 | hero_id, key_points, counters(list[int]), synergizes_with(list[int]), description, tips_for_beginners |
| Card | 对局内基础卡牌 | id, name, card_type(行动牌/战法牌/装备牌/延时牌/基本牌), card_desc, card_detail, card_amount |
| IncrementalUpdate | 增量更新结构 | added/modified/removed 三类变更数据 |

**官网数据解析说明**：Hero、Card 模型通过 alidation_alias 支持中文字段名映射（如 角色ID、名称、体力上限），方便官网爬虫数据直接解析。

### 数据管理器 (src/data/manager.py)

核心功能：
- **加载/保存**：从 JSON 文件读写武将、相性、攻略
- **查询**：按 ID 查询武将、查询武将间相性（双向一致）、获取攻略
- **增删改**：完整 CRUD 操作
- **增量更新**：通过 IncrementalUpdate 模型批量应用数据变更
- **势力筛选**：按势力过滤武将列表

### 样本数据

data/ 目录包含 **149 个武将**数据（含完整技能描述），6 条相性关系和 4 份攻略样本供开发和测试使用。武将 ID 为整数编号（1-149），相性和攻略中通过 int ID 引用武将。

### 单元测试

	ests/test_models.py 包含 **25 个测试用例**，覆盖：
- Skill 创建、空值校验
- Hero 创建、别名解析、默认值、边界校验
- SynergyScore 评分校验、评级枚举、取值范围
- HeroGuide int ID 引用
- Card 别名解析、CardType 枚举
- IncrementalUpdate 增量结构

---

## 后续阶段（计划）

| 阶段 | 内容 | 状态 |
|------|------|------|
| 二 | 数据采集工具（官网爬虫 2.1/2.2 + AI 批量生成 2.3-2.5，使用 deepseek-v4-pro 模型） | ✅ 已完成 |
| 三 | PyQt 桌面应用（主窗口、武将浏览器、API 配置） | 待开发 |
| 四 | 屏幕采集（MuMu 截图、轮廓检测、OCR） | 待开发 |
| 五 | 推荐引擎（相性查询、推荐展示） | 待开发 |
| 六 | 文档与收尾 | 待开发 |

---

## 开发规范

参见 AGENTS.md。
