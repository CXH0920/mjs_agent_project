# 名将杀 Agent — 项目总览

> 文档日期：2026-07-22
> 项目路径：`G:\py_savepoint\test_project`  
> 远程仓库：`gitee.com:chen-xianghao920/test_project.git`

## 项目简介

名将杀 Agent 是一款面向[名将杀手游](https://mjs.ztgame.com/)的桌面辅助工具。它提供武将数据库查询、AI 批量攻略生成、武将相性分析、实时屏幕采集与 OCR 武将识别等功能，帮助玩家在游戏中快速决策。

## 核心功能

- **资料库浏览** — 在“武将资料”中查询武将详情、技能和攻略；在“卡牌图鉴”中只读浏览官方卡牌及维护独立的版本追加信息
- **选将推荐** — 4×2 网格展示推荐武将，集成相性评分、胜率排名与 OCR 截图导入
- **对局攻略** — 2×2 展示四名武将，支持 ADB/本地图片导入并加载 2v2 胜率
- **AI 攻略生成** — 通过 DeepSeek API 或浏览器自动化批量生成武将攻略
- **AI 相性评分** — 全量/指定武将的相性评分，支持 2~8 武将两两配对
- **屏幕采集与 OCR** — 通过 ADB 连接模拟器截图，OpenCV 模板匹配 + PaddleOCR 识别武将名
- **实时轮询** — 统一截图后独立检测武将选择页和对局攻略页，分别维护任务激活状态与冷却时间
- **官方数据导入** — 可独立或同时导入 2v2/武将放逐榜单图片；按表格行写入三份 CSV，显示 OCR 进度，并以词表候选、逐字补识别和待复核保证名称可靠性

## 技术栈

| 层次 | 技术 |
|------|------|
| 桌面 UI | PySide6（Qt for Python） |
| 数据模型 | Pydantic v2（数据校验与序列化） |
| AI 生成 | httpx（DeepSeek API 同步请求）/ Playwright（浏览器自动化） |
| 屏幕采集 | ADB（Android Debug Bridge）exec-out 截图 |
| 图像处理 | OpenCV（模板匹配、表格横线检测）、Pillow（图像格式转换） |
| OCR 识别 | PaddleOCR + 编辑距离矫正 + 汉字特征评分 |
| 数据持久化 | JSON + CSV 文件（原子写入，无数据库依赖） |
| 测试 | pytest（当前 `--collect-only` 收集 323 项，以命令输出为准） |
| 异步通信 | QProcess（子进程管理）+ Qt Signal/Slot |

## 整体目录结构

```
test_project/
├── src/
│   ├── main.py                  # 应用入口
│   ├── config/                  # 配置管理（.env 解析、日志配置）
│   ├── data/                    # 数据模型与数据管理层
│   ├── scraper/                 # 爬虫与 AI 批量生成层
│   ├── business/                # 业务服务层（QProcess、OCR 与官方榜单导入编排）
│   ├── capture/                 # 屏幕采集层（ADB 连接与截图）
│   ├── ocr/                     # OCR 识别层（模板匹配 + PaddleOCR）
│   └── ui/                      # PySide6 用户界面层
├── data/                        # 数据文件（JSON + 2v2 胜率/出场、放逐 CSV）
├── images/                      # 武将头像（165 个 PNG）
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
┌──────────────────────────────────────────────────────────┐
│  UI 层 (src/ui/)                                         │
│  PySide6 窗口、对话框、推荐面板、武将浏览器、对局攻略页   │
│  信号连接 → 业务服务 → 子进程 → 数据刷新                 │
├──────────────────────────────────────────────────────────┤
│  业务服务层 (src/business/)                               │
│  QProcess 管理、ADB 截图编排、OCR 轮询控制                │
│  无 UI 引用，通过 Signal 通信                             │
├──────────────────────────────────────────────────────────┤
│  采集层 (src/scraper/ + src/capture/ + src/ocr/)         │
│  官网爬虫 / AI 生成 / ADB 截图 / 模板匹配 / PaddleOCR    │
├──────────────────────────────────────────────────────────┤
│  数据层 (src/data/)                                       │
│  Pydantic 模型 + DataFacade + JSON 持久化                │
└──────────────────────────────────────────────────────────┘
```

## 子模块文档索引

| # | 模块 | 目录 | 主要职责 |
|---|------|------|---------|
| 1 | [应用入口与配置](./module_config.md) | `src/main.py` + `src/config/` | 应用启动、环境配置、日志初始化 |
| 2 | [数据模型与数据管理](./module_data.md) | `src/data/` | Pydantic 模型定义、CRUD 操作、JSON 持久化 |
| 3 | [爬虫与数据采集](./module_scraper.md) | `src/scraper/`（非 AI 部分） | 官网 JS chunk 解析、数据清洗、头像下载 |
| 4 | [AI 批量生成](./module_ai_batch.md) | `src/scraper/ai_*.py` | AI 攻略/相性生成、JSON 提取、双模式生成器 |
| 5 | [业务服务层](./module_business.md) | `src/business/` | QProcess 子进程管理、服务编排、官方榜单图片导入 |
| 6 | [屏幕采集与 OCR](./module_capture_ocr.md) | `src/capture/` + `src/ocr/` | ADB 截图、模板匹配、PaddleOCR 识别 |
| 7 | [UI 界面层](./module_ui.md) | `src/ui/` | 主窗口、对话框体系、推荐面板、武将浏览器 |
