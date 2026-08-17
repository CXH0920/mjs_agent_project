# 模块：应用入口与配置

> 对应目录：`src/main.py` + `src/config/`
> 职责：应用启动入口、环境变量与 .env 配置管理、统一日志初始化

---

## 一、模块职责

本模块负责两件事：
1. **应用启动** — 创建 `QApplication`，安装 Qt 标准控件中文翻译，设置并维护窗口图标，初始化日志系统，构建 `MainWindow` 并进入事件循环
2. **配置管理** — 从 `config.env` 文件、环境变量和默认值三级加载配置，并维护版本控制的模型价格表

---

## 二、文件结构

```
src/
├── main.py                  # 应用入口（QApplication + MainWindow 构建）
├── ui/app/app_icon.py        # 应用图标加载、缓存与顶层窗口图标维护
├── ui/chinese_translator.py  # Qt 标准控件中文翻译
├── config/
│   ├── __init__.py
│   ├── env.py               # .env 与模型价格配置解析/加载/保存（原子写入）
│   └── logging_config.py    # 统一日志配置（按模块拆分 + 文件轮转）
```

---

## 三、核心逻辑

### 3.1 应用启动流程

```python
# src/main.py
setup_logging()          # 初始化日志系统
app = QApplication(sys.argv)
install_chinese_qt_translator(app)  # 标准按钮和文件对话框使用中文
splash.show()            # 在主窗口构建期间显示启动页
window = MainWindow()    # 构建主窗口
window.show()
splash.finish(window)    # 主窗口显示后关闭启动页
sys.exit(app.exec())     # 进入事件循环
```

启动顺序有严格讲究：
1. **启动页先显示** — `QSplashScreen` 在主窗口构建前显示，避免用户看到 Windows/Qt 初始化期间的空白窗口
2. **QApplication 先于 MainWindow** — Qt 要求先创建应用对象，才能安全创建 UI 组件；中文翻译器也必须在创建任何窗口前安装，以覆盖 Qt 自动生成的保存、取消、是、否等按钮。未匹配的 Qt 文案必须返回“未翻译”结果，保留框架原始文本，避免影响菜单快捷键等内部格式化。
3. **主窗口就绪后关闭** — `splash.finish(window)` 在主窗口显示后关闭启动页，异常时显式关闭，避免残留

### 3.2 配置加载优先级

```
config.env > 环境变量 > 默认值
```

`src/config/env.py` 的核心函数：

```python
def parse_env_file(path) -> dict[str, str]:
    # 读取 .env 文件，返回所有键值对（值均为字符串）

def load_env_config(path) -> dict:
    # 解析后通过 key_mapping 映射为内部小写 key，完成类型转型（含 LOG_TO_FILE 布尔值）

def get_runtime_params() -> dict:
    # 返回请求、超时、日志等级及 log_to_file（bool）运行参数

def get_api_config() -> dict:
    # 合并 config.env + 环境变量 + 默认值
    # 返回 api_key / api_url / model / requests_per_minute / max_retries / http_timeout

def get_mumu_config() -> dict:
    # 获取模拟器 ADB 路径/端口/OCR 开关/轮询/自动跳转及 GPU/CPU 推理配置

def save_env_file(path, data):
    # 原子写入：先写 .env.tmp → replace 覆盖原文件
```

> OCR 推理配置：`MUMU_OCR_USE_GPU`（默认 false，CPU 推理）与 `MUMU_OCR_CPU_THREADS`（默认 6，CPU 线程上限）由 `load_env_config()` 完成类型转型（GPU 按布尔、线程数按整数），经 `get_mumu_config()` 提供给 `paddle_loader.create_paddle_ocr()`。

### 3.3 日志系统

`src/config/logging_config.py` 通过 `ModuleFilter` 按 logger name 前缀分派日志到不同文件：

| logger name 前缀 | 目标文件 |
|-----------------|----------|
| `src.scraper` / `subprocess.official` | `logs/scraper/official.log` |
| `src.scraper.ai` / `subprocess.ai` | `logs/scraper/ai_generation.log` |
| `src.business.fetching` | `logs/business/fetching.log` |
| `src.business.emulator` | `logs/business/emulator.log` |
| `src.business.recognition` | `logs/business/recognition.log` |
| `src.business.analysis` / `src.business.maintenance` | `logs/business/business.log` |
| `src.data` / `src.ocr` / `src.capture` | 各自同名目录下的日志文件 |
| 其他 `subprocess.*` | `logs/subprocess/unclassified.log` |
| 其他（含 `src.ui.*`） | `logs/app.log` |

每条记录只进入一个目标文件。每个日志文件最大 10MB，保留 5 个备份自动轮转；已有旧日志不会自动删除或迁移。

### 3.4 模型价格

- `config/model_pricing.json` 是版本控制的模型价格来源。未知模型不会套用默认价格，而是返回“无法自动估算”。
- 价格文件包含 `currency`、`unit`、`updated_at` 和 `models`；计价单位为“百万tokens”，每个模型维护输入、输出、可选缓存命中单价。
- `load_pricing_config(path)` 负责读取价格表，`save_pricing_config(path, data)` 负责 UTF-8 无 BOM、LF 换行的原子写入。
- API 配置对话框的“价格配置”页签直接维护该文件，保存前会校验模型名称唯一且单价为非负数。

---

## 四、关键代码片段

### 4.1 原子写入 .env

```python
def save_env_file(env_path: Path, data: dict[str, str]) -> None:
    tmp_path = env_path.with_suffix(".env.tmp")
    lines = [f"{k}={v}\n" for k, v in data.items()]
    tmp_path.write_text("".join(lines), encoding="utf-8")
    tmp_path.replace(env_path)  # 原子替换
```

> **设计思路：** 直接写原文件如果中途崩溃会导致 .env 半损坏。`.tmp` → `.replace()` 在 NTFS 上是原子操作，写入成功前原文件不变。

### 4.2 日志路由的幂等性

```python
def setup_logging():
    root = logging.getLogger()
    for handler in root.handlers[:]:
        if getattr(handler, "_mjs_managed_handler", False):
            root.removeHandler(handler)
            handler.close()
    # 保留外部 Handler，按当前配置重建项目 Handler
    # ... 配置 handler、formatter、ModuleFilter ...
```

> **设计思路：** 只清理项目自身创建的 Handler，避免重复注册，同时不破坏 pytest 或宿主程序的外部日志 Handler。重复调用时新的级别和文件开关可以生效。

---

## 五、接口说明

本模块不提供外部 API 接口，但提供以下公共函数：

| 函数 | 文件 | 返回 | 说明 |
|------|------|------|------|
| `parse_env_file(path)` | `env.py` | `dict[str, str]` | 解析 .env 文件 |
| `load_env_config(path)` | `env.py` | `dict` | 解析并类型转型 |
| `get_api_config()` | `env.py` | `dict` | 获取 API 配置 |
| `load_pricing_config(path)` | `env.py` | `dict` | 加载模型价格配置 |
| `save_pricing_config(path, data)` | `env.py` | `None` | 原子保存模型价格配置 |
| `get_model_pricing(model)` | `env.py` | `dict \| None` | 查询模型单价，未知模型返回 `None` |
| `get_mumu_config()` | `env.py` | `dict` | 获取模拟器配置 |
| `save_env_file(path, data)` | `env.py` | `None` | 原子写入 .env |
| `setup_logging()` | `logging_config.py` | `None` | 初始化日志系统（幂等） |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 被依赖 | — | 所有模块都通过 `from src.config.env import ...` 获取配置 |
| 被依赖 | `src.rag.config` | RAG 语料/向量索引与预算配置（RAG_ENABLED / RAG_TOP_K / RAG_PROMPT_CHARS / RAG_BROWSER_PROMPT_CHARS / RAG_SYNERGY_PROMPT_CHARS / RAG_MODEL_DIR），由 AI 批量生成模块使用 |
| 依赖 | `src.ui.app.main_window` | 应用入口创建 MainWindow 实例 |
| 依赖 | `src.business.recognition.ocr_worker` | 预热 PaddleOCR 模型 |
