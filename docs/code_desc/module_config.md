# 模块：应用入口与配置

> 对应目录：`src/main.py` + `src/config/`
> 职责：应用启动入口、环境变量与 .env 配置管理、统一日志初始化

---

## 一、模块职责

本模块负责两件事：
1. **应用启动** — 创建 `QApplication`，设置窗口图标，初始化日志系统，构建 `MainWindow` 并进入事件循环
2. **配置管理** — 从 `config.env` 文件、环境变量和默认值三级加载配置，提供统一的配置访问接口

---

## 二、文件结构

```
src/
├── main.py                  # 应用入口（QApplication + MainWindow 构建）
├── config/
│   ├── __init__.py
│   ├── env.py               # .env 解析/加载/保存（原子写入）
│   └── logging_config.py    # 统一日志配置（按模块拆分 + 文件轮转）
```

---

## 三、核心逻辑

### 3.1 应用启动流程

```python
# src/main.py
app = QApplication(sys.argv)
warmup()                 # 预加载 PaddleOCR 模型（界面出现前完成）
setup_logging()          # 初始化日志系统
window = MainWindow()    # 构建主窗口
window.show()
sys.exit(app.exec())     # 进入事件循环
```

启动顺序有严格讲究：
1. **PaddleOCR 预热** — 在窗口出现前加载 OCR 模型（约 2-3 秒），避免用户看到窗口后操作时卡顿
2. **QApplication 先于 MainWindow** — Qt 要求事件循环先启动，才能安全创建 UI 组件
3. **DataFacade 双次加载** — `MainWindow.__init__()` 和 `_load_data()` 中各调用一次 `setup_logging`，通过幂等性检测防重复

### 3.2 配置加载优先级

```
config.env > 环境变量 > 默认值
```

`src/config/env.py` 的核心函数：

```python
def parse_env_file(path) -> dict[str, str]:
    # 读取 .env 文件，返回所有键值对（值均为字符串）

def load_env_config(path) -> dict:
    # 解析后通过 key_mapping 映射为内部小写 key，完成类型转型

def get_api_config() -> dict:
    # 合并 config.env + 环境变量 + 默认值
    # 返回 api_key / api_url / model / requests_per_minute / max_retries / http_timeout

def get_mumu_config() -> dict:
    # 获取模拟器 ADB 路径/端口/OCR 开关/轮询配置

def save_env_file(path, data):
    # 原子写入：先写 .env.tmp → replace 覆盖原文件
```

### 3.3 日志系统

`src/config/logging_config.py` 通过 `ModuleFilter` 按 logger name 前缀分派日志到不同文件：

| logger name 前缀 | 目标文件 |
|-----------------|----------|
| `src.scraper.ai_` | `logs/scraper/ai_batch.log` |
| `src.scraper` / `src.capture` / `src.ocr` | `logs/scraper/scraper.log` |
| `src.business` | `logs/business/business.log` |
| `subprocess.stdout` | `logs/subprocess/stdout.log` |
| `subprocess.stderr` | `logs/subprocess/stderr.log` |
| 其他（含 `src.ui.*`） | `logs/app.log` |

每个日志文件最大 10MB，保留 5 个备份自动轮转。

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
    if root.handlers:
        return  # 已配置过，跳过
    # ... 配置 handler、formatter、ModuleFilter ...
```

> **设计思路：** 应用入口和 CLI 脚本都可能调用 `setup_logging()`。没有幂等检查会在第二次调用时重复注册 handler，导致日志打印两遍。

---

## 五、接口说明

本模块不提供外部 API 接口，但提供以下公共函数：

| 函数 | 文件 | 返回 | 说明 |
|------|------|------|------|
| `parse_env_file(path)` | `env.py` | `dict[str, str]` | 解析 .env 文件 |
| `load_env_config(path)` | `env.py` | `dict` | 解析并类型转型 |
| `get_api_config()` | `env.py` | `dict` | 获取 API 配置 |
| `get_mumu_config()` | `env.py` | `dict` | 获取模拟器配置 |
| `save_env_file(path, data)` | `env.py` | `None` | 原子写入 .env |
| `setup_logging()` | `logging_config.py` | `None` | 初始化日志系统（幂等） |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 被依赖 | — | 所有模块都通过 `from src.config.env import ...` 获取配置 |
| 依赖 | `src.ui.main_window` | 应用入口创建 MainWindow 实例 |
| 依赖 | `src.ocr.ocr_loader` | 预热 PaddleOCR 模型 |
