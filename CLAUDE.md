# 名将杀 Agent

名将杀桌面辅助工具，面向名将杀手游的轻度玩家，运行于 PC 端 MuMu 模拟器。
核心功能：**选将推荐** + **武将数据库查询**。

## 快速开始

```bash
# 创建环境
conda env create -f environment.yml
conda activate myenv

# 运行测试
pytest tests/ -v

# 启动桌面应用
python -m src.main

# 数据采集（官网爬虫）
python -m src.scraper.official

# 增量采集
python -m src.scraper.incremental --incremental

# AI 批量生成攻略
python -m src.scraper.ai_batch --guide
```

## 技术栈

| 层 | 技术 |
|---|---|
| UI | PySide6 |
| 数据模型 | Pydantic v2 |
| 数据存储 | JSON 文件 |
| 爬虫 | urllib + BeautifulSoup4 |
| AI API | httpx → DeepSeek |
| 测试 | pytest |

## 项目结构

```
test_project/
├── src/
│   ├── main.py                    # 应用入口
│   ├── config/
│   │   └── env.py                 # .env 解析/加载/保存
│   ├── data/
│   │   ├── models.py              # Pydantic 数据模型
│   │   ├── manager.py             # 统一入口 + 增量更新函数
│   │   ├── hero_manager.py        # Hero CRUD + JSON 持久化
│   │   ├── synergy_manager.py     # SynergyScore CRUD + JSON
│   │   └── guide_manager.py       # HeroGuide CRUD + JSON
│   ├── scraper/
│   │   ├── crawler.py             # 爬虫核心（公开 API）
│   │   ├── official.py            # 官网全量爬虫
│   │   ├── incremental.py         # 增量/指定爬虫
│   │   ├── ai_batch.py            # DeepSeek API 批量生成（入口）
│   │   ├── ai_guide.py            # 攻略生成循环
│   │   └── ai_synergy.py          # 相性评分生成循环
│   ├── business/
│   │   ├── fetch_service.py       # 采集业务（QProcess 管理）
│   │   └── guide_fetch_service.py # 攻略生成业务（QProcess）
│   └── ui/
│       ├── main_window.py         # 主窗口
│       ├── hero_browser.py        # 武将浏览
│       ├── settings_dialog.py     # API 配置对话框
│       ├── fetch_dialog.py        # 武将获取选择
│       ├── guide_fetch_dialog.py  # 攻略获取选择
│       ├── cost_confirm_dialog.py # 成本确认
│       └── guide_progress_dialog.py # 进度条
├── data/
│   ├── heroes.json                # 149 个武将
│   ├── synergies.json             # 相性评分
│   └── guides.json                # 武将攻略
├── tests/
│   ├── test_models.py             # 25 tests
│   ├── test_ai_batch.py           # 33 tests
│   ├── test_hero_manager.py       # 13 tests
│   ├── test_synergy_manager.py    # 13 tests
│   ├── test_guide_manager.py      # 11 tests
│   ├── test_incremental_update.py # 8 tests
│   └── test_ui.py                 # 4 tests
└── docs/
    ├── field_mapping.md
    └── prompts/
        ├── hero_guide.md
        └── synergy_score.md
```

## 架构

### 四层架构

```
UI 层 (PySide6)     → main_window.py, hero_browser.py, 各对话框
业务层 (Business)   → fetch_service.py, guide_fetch_service.py (QProcess)
数据层 (Data)       → models.py (Pydantic) + hero_manager.py / synergy_manager.py / guide_manager.py (JSON)
采集层 (Capture)    → 待开发 (screen.py, detector.py, ocr.py)
```

爬虫层横切数据层和外部数据源：crawler.py 为核心，official.py 全量采集，incremental.py 增量/指定采集，ai_batch.py AI 生成。

### 数据流

官网页面 → crawler.py 解析 JS chunk → official.py/incremental.py 清洗校验 → data/heroes.json
DeepSeek API → ai_batch.py → ai_guide.py / ai_synergy.py → data/guides.json + data/synergies.json
JSON 文件 → Manager 加载 → UI 展示
用户操作 → MainWindow → QProcess → 爬虫脚本

### 配置加载优先级

config.env > 环境变量 > 默认值（定义在 src/config/env.py）

## 关键约定

### 测试约定
- 使用纯 pytest（不继承 unittest.TestCase）
- 测试类命名用 `Test` 前缀，方法用 `test_` 前缀
- 文件 IO 测试使用 `tempfile` 避免影响真实数据
- Manager 测试使用 `_make_*` 辅助方法构造测试数据
- `sys.path` 在测试文件内手动添加 `../src`

### 代码约定
- 所有源文件使用 `from __future__ import annotations` 启用 PEP 604
- 使用 `pathlib.Path` 而非 `os.path`
- 使用 `logging` 而非 `print`（CLI 输出除外）
- 类型注解：函数参数和返回值必须标注类型
- 注释解释 WHY 而非 WHAT

### 数据模型约定
- 官网数据通过 `validation_alias`（中文字段名）映射到 Pydantic 模型
- 大模型生成的数据通过 `model_dump(mode="json")` 序列化
- Skill 的 `description` 和 `settlement` 从 HTML 拆分
- Hero 通过 int ID 引用，SynergyScore 和 HeroGuide 通过 int hero_id 关联
- Synergy 双向一致：(A,B) 和 (B,A) 映射到同一 key（排序后）

### Manager 约定
- 三个 Manager 各自独立，遵循 SRP
- CRUD 操作：add（唯一约束，已存在抛 ValueError）、update（upsert）、delete
- Manager 使用 `_` 前缀私有变量 `_heroes`、`_synergies`、`_guides` 缓存数据
- 支持 `load()` / `save()` JSON 持久化，默认路径在 `data/` 目录

### scraper 约定
- `crawler.py` 为公共模块，提供 `fetch()` / `find_chunk_url()` / `extract_js_array()` / `js_to_json()` / `transform()` / `validate_heroes()` 公开 API
- CLI 入口使用 `python -m` 执行
- AI 生成支持断点续传（跳过已有项）
- HTML 清洗：先拆段落后再逐段 clean_html
- Json 原子写入：先写 `.tmp` 文件，再 `replace` 原文

## 可用命令

```bash
# 运行所有测试
pytest tests/ -v

# 运行单个测试文件
pytest tests/test_models.py -v

# 启动应用
python -m src.main

# 官网全量采集
python -m src.scraper.official [--dry-run] [--output path] [--verbose]

# 增量采集
python -m src.scraper.incremental --incremental [--dry-run] [--output path]

# 指定武将采集
python -m src.scraper.incremental --hero 诸葛亮,关羽
python -m src.scraper.incremental --hero-id 52,114

# AI 攻略生成
python -m src.scraper.ai_batch --guide [--dry-run] [--heroes-file path]

# AI 相性评分生成
python -m src.scraper.ai_batch --synergy [--dry-run] [--score-threshold 0]
```

## 外部依赖

- **PySide6** — 桌面 UI
- **Pydantic** — 数据模型校验
- **httpx** — AI API 请求
- **beautifulsoup4** — HTML 解析（备用）
- **opencv-python / easyocr / mss** — 待开发阶段（截图/OCR）
- **pytest** — 测试框架

## API 配置

在 `config.env` 中配置（已 gitignored）：

```env
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_API_URL=https://api.deepseek.com/v1/chat/completions
DEEPSEEK_MODEL=deepseek-v4-pro
REQUESTS_PER_MINUTE=30
HTTP_TIMEOUT=300
MAX_RETRIES=3
```

定价：输入 CNY 3/百万 tokens，输出 CNY 6/百万 tokens（deepseek-v4-pro 缓存未命中）。
