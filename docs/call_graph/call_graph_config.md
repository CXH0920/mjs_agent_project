# 调用链路：应用入口与配置

> 对应源码：`src/main.py` + `src/config/`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。

---

## 当前实现基线（2026-07-22）

`get_api_config()` 优先级为 `config.env > 环境变量 > 默认值`；默认 API 地址为 `https://api.deepseek.com/v1/chat/completions`，默认模型为 `deepseek-v4-pro`。`get_runtime_params()` 和 `get_mumu_config()` 经 `load_env_config()` 完成字段映射与类型转换。

```
main() -> get_runtime_params() -> setup_logging()
MainWindow.__init__() -> get_mumu_config() -> CaptureService/OcrService.update_config()
ai_batch.main() -> get_api_config()
```

`save_env_file()` 先保留原文件注释和未修改键，再写入 `.env.tmp` 并 `replace()` 原子替换。当前启动不主动执行 OCR warmup：`main()` 创建 QApplication 后直接创建 `MainWindow`，OCR 引擎由首次提交 OCR 任务时延迟加载，避免启动阶段重复构造数据门面。

## 一、应用启动链路

### 1.1 主函数完整调用链

```
[操作系统] python src/main.py
  -> main()                                                    [应用入口]
    -> setup_logging(log_level="INFO", log_to_file=True)        [日志初始化 (幂等)]
       -> [已配置] return (跳过)
       -> [首次] root = logging.getLogger()
       -> 创建 logger: "ui"/"scraper"/"business"/"subprocess"
       -> 创建 FileHandler × 日志分类数 + RotatingFileHandler(10MB × 5)
       -> 创建 ModuleFilter (按 logger name 前缀分发)
       -> 设置 root handler 避免未匹配日志丢失
    -> os.environ.setdefault("QT_LOGGING_RULES", "...")         [抑制 Qt 字体回退日志]
    -> sys.stdout.reconfigure(encoding="utf-8")                 [Windows UTF-8 支持]
    -> QApplication.setHighDpiScaleFactorRoundingPolicy(...)     [高 DPI 支持]
    -> app = QApplication(sys.argv)                             [创建 Qt 应用]
    -> app.setApplicationName("名将杀 Agent")
    -> app.setOrganizationName("MingJiangSha")
    -> [Windows] ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(...)
       [修正 Windows 任务栏图标, 静默失败]
    -> install_app_icon(app)                                  [加载/缓存图标并安装恢复器]
       -> load_app_icon()                                     [基于源码绝对路径加载 mjs.ico]
       -> app.setWindowIcon(icon)                             [设置应用默认图标]
       -> app.installEventFilter(_AppIconKeeper)              [窗口显示/激活时恢复图标]

    -> app.setStyleSheet(GLOBAL_STYLE)                          [设置全局样式表]
    -> window = MainWindow()                                    [创建主窗口]
       -> [完整 MainWindow 初始化链见 call_graph_ui.md]
    -> window.show()                                            [显示窗口]
    -> sys.exit(app.exec())                                     [进入 Qt 事件循环]
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `main()` | `src/main.py` | Python 入口 | `get_runtime_params()`, `setup_logging()`, `QApplication()`, `MainWindow()` |
| `setup_logging()` | `config/logging_config.py` | `main()` | `logging.getLogger()`, `ModuleFilter`, `RotatingFileHandler` |
| `MainWindow.__init__()` | `ui/main_window.py` | `main()` | 创建服务、`_load_data()`, `_setup_ui()` |
| `DataFacade.load_all()` | `data/manager.py` | `MainWindow._load_data()` | `HeroManager.load()`, `SynergyManager.load()`, `GuideManager.load()` |

> **启动顺序说明：** 主窗口构造时完成数据加载和服务配置；PaddleOCR 由 `OcrWorker`/`ocr_loader` 在首次 OCR 任务中延迟初始化。这样启动链路只负责进入 Qt 事件循环，首次识别才承担模型加载成本。

---

## 二、配置加载链路

### 2.1 配置加载优先级

```
config.env (文件) > 环境变量 > 默认值
```

### 2.2 API 配置加载

```
get_api_config()
  -> parse_env_file(env_path)                                  [读取 .env 文件]
     -> [文件不存在] return {}                                  [静默跳过]
     -> [读取] lines = Path.read_text().splitlines()
     -> 逐行解析: key=value, 跳过 # 注释和空行
     -> return dict
  -> config.env 优先；未配置时读取 DEEPSEEK_API_KEY / OPENAI_API_KEY 环境变量
  -> 类型转换 + 默认值填充:
     -> api_key: from env or ""                                 [无默认值]
     -> api_url: from config.env or "https://api.deepseek.com/v1/chat/completions"
     -> model: from config.env or "deepseek-v4-pro"
     -> requests_per_minute: from env or 30 (int)
     -> max_retries: from env or 3 (int)
     -> http_timeout: from env or 300 (int)
     -> log_to_file: from env or True (bool)
  -> return config dict
```

### 2.3 模拟器配置加载

```
get_mumu_config()
  -> parse_env_file(env_path)                                  [同上读取 .env]
  -> dict 构建:
     -> mumu_adb_path: from env or ""                           [ADB 路径]
     -> mumu_adb_port: from env or 0 (int)                      [ADB 端口]
     -> mumu_ocr_enabled: from env or False (bool)              [OCR 启用]
     -> mumu_ocr_poll_mode: from env or False (bool)            [轮询模式]
     -> mumu_ocr_poll_interval: from env or 2 (int)             [轮询间隔（秒）]
     -> mumu_ocr_match_threshold: from env or 0.8 (float)       [兼容旧版通用阈值]
     -> mumu_hero_selection_threshold: from env or 0.8 (float)  [选将模板阈值]
     -> mumu_hero_selection_cooldown: from env or 180 (int)     [选将冷却秒数]
     -> mumu_match_guide_threshold: from env or 0.8 (float)    [对局攻略模板阈值]
  -> return config dict
```

### 2.4 配置文件保存

```
save_env_file(env_path, data: dict)
  -> 读取原文件并保留注释/未修改键
  -> 合并 data 中的新值或覆盖已有键
  -> tmp_path = env_path.with_suffix(".env.tmp")               [临时文件]
  -> tmp_path.write_text("\\n".join(lines) + "\\n", encoding="utf-8") [UTF-8 写入 tmp]
  -> tmp_path.replace(env_path)                                [原子替换]
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `get_api_config()` | `config/env.py` | `ai_batch.py:main()`, UI 层 | `parse_env_file()`, `os.environ.get()`, 默认值 |
| `get_mumu_config()` | `config/env.py` | `MainWindow.__init__()`, `MumuConfigDialog` | `parse_env_file()`, 类型转换 |
| `parse_env_file(path)` | `config/env.py` | `get_api_config()`, `get_mumu_config()` | `Path.read_text()`, 逐行解析 |
| `load_env_config(path)` | `config/env.py` | 外部 | `parse_env_file()`, key_mapping, 类型转换 |
| `save_env_file(path, data)` | `config/env.py` | `MumuConfigDialog` 保存, `SettingsDialog` 保存 | `Path.write_text()`, 原子替换 |

### 2.5 模型价格配置

成本估算读取独立 JSON，不混入 `config.env`：

```
prompt_utils.estimate_cost(count, mode, model)
  -> get_model_pricing(model)
     -> load_pricing_config(config/model_pricing.json)
     -> 校验 input_per_million / output_per_million
  -> 计算输入/输出 token 与费用

SettingsDialog / 管理脚本
  -> save_pricing_config(path, data)
     -> json.dumps(ensure_ascii=False)
     -> *.tmp.write_text(encoding="utf-8", newline="\\n")
     -> replace(path)
```

| 函数 | 文件 | 说明 |
|------|------|------|
| `load_pricing_config(path)` | `config/env.py` | 读取并校验价格 JSON，异常时返回空模型表 |
| `get_model_pricing(model)` | `config/env.py` | 返回指定模型单价，未知或非法配置返回 `None` |
| `save_pricing_config(path, data)` | `config/env.py` | UTF-8、LF、无 BOM 原子写入 |

---

## 三、日志系统链路

### 3.1 日志初始化

```
setup_logging(log_level="INFO", log_to_file=True)
  -> root = logging.getLogger()
  -> [root.handlers 非空] return                               [幂等: 已配置则跳过]
  -> root.setLevel(logging.DEBUG)                               [root 不设限]
  -> [log_to_file]
     -> log_dir = "logs" / {category}                           [分类子目录]
        -> "logs/scraper/" (crawler + ocr + capture)
        -> "logs/scraper/ai_batch.log" (AI 生成日志独立)
        -> "logs/business/business.log" (QProcess 日志)
        -> "logs/subprocess/stdout.log" + "stderr.log" (子进程日志)
        -> "logs/app.log" (UI 和其他)
     -> RotatingFileHandler(maxBytes=10MB, backupCount=5)       [每日志分类]
     -> ModuleFilter(logger_prefix)                             [按 logger name 前缀匹配]
  -> [控制台] logging.StreamHandler(sys.stdout)
  -> [UI 日志] QPlainTextEdit 日志处理器 (仅 app 日志)
```

```
日志分发规则:
  logger name 前缀 → 目标文件:
  "src.scraper.ai"     → logs/scraper/ai_batch.log
  "src.scraper"        → logs/scraper/scraper.log
  "src.capture"        → logs/scraper/scraper.log (同上)
  "src.ocr"            → logs/scraper/scraper.log (同上)
  "src.business"       → logs/business/business.log
  "subprocess.stdout"  → logs/subprocess/stdout.log
  "subprocess.stderr"  → logs/subprocess/stderr.log
  (其他)               → logs/app.log
```

| 函数 | 所在文件 | 说明 |
|------|----------|------|
| `setup_logging(level, log_to_file)` | `config/logging_config.py` | 初始化日志系统（幂等） |
| `ModuleFilter(logger_prefix)` | `config/logging_config.py` | 自定义 Filter: logger.name.startswith(prefix) |

---

## 四、外部调用关系总览

### 4.1 本模块被外部调用

```
src.main.py 是应用唯一的直接执行入口。

src.config.env 的函数被几乎所有模块调用:
  get_api_config()  ← src.scraper.ai_batch.py
                   ← src.ui.app.main_window.py
                   ← src.ui.configuration.settings_dialog.py

  get_mumu_config() ← src.ui.app.main_window.py
                    ← src.ui.configuration.mumu_config_dialog.py

  save_env_file()   ← src.ui.app.main_window.py (_open_mumu_config)
                    ← src.ui.configuration.settings_dialog.py (_on_save)

  setup_logging()   ← src.main.py
                    ← 各 CLI 子进程入口 (official.py / incremental.py / ai_batch.py)
```

### 4.2 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| `src.data.manager.DataFacade` | 加载武将名列表 |
| `src.ui.app.main_window.MainWindow` | 创建主窗口 |
| `src.ui.shared.style.GLOBAL_STYLE` | 全局样式表 |
| `src.ocr.ocr_loader` / `OcrWorker` | 首次 OCR 任务时延迟创建识别器 |

---

## 五、函数清单总表

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `main()` | `src/main.py` | Python 入口 | `get_runtime_params()`, `setup_logging()`, `QApplication()`, `MainWindow()` |
| `setup_logging(level, file)` | `config/logging_config.py` | `main()`, 各 CLI 入口 | `RotatingFileHandler`, `ModuleFilter` |
| `get_api_config()` | `config/env.py` | `ai_batch.py`, `settings_dialog` | `parse_env_file()`, `os.environ.get()`, 默认值填充 |
| `get_mumu_config()` | `config/env.py` | `MainWindow.__init__()`, `mumu_config_dialog` | `parse_env_file()`, 类型转换 |
| `parse_env_file(path)` | `config/env.py` | `get_api_config()`, `get_mumu_config()` | `Path.read_text()`, 逐行解析 |
| `load_env_config(path)` | `config/env.py` | 外部 | `parse_env_file()`, key_mapping |
| `save_env_file(path, data)` | `config/env.py` | `MainWindow`, `SettingsDialog` | `Path.write_text()`, 原子替换 |
