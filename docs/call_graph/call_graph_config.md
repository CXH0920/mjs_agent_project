# 调用链路：应用入口与配置

> 对应源码：`src/main.py` + `src/config/`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。

---

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

    -> [提前初始化 PaddleOCR 模型]
       -> from src.ocr.recognizer import GeneralRecognizer
       -> from src.data.manager import DataFacade, DEFAULT_*
       -> facade = DataFacade(...)                              [数据门面（空）]
       -> facade.load_all()                                     [加载数据文件]
       -> hero_names = [h.name for h in facade.heroes.list_heroes()]
       -> recognizer = GeneralRecognizer(hero_names=hero_names) [创建识别器]
       -> recognizer.warmup()                                   [预热 OCR 模型]
          -> self._engine (property)                            [首次调用→延迟初始化 PaddleOCR]
          -> _load_char_info()                                  [加载汉字特征缓存]
          -> pypinyin.pinyin("预热")                            [预热 pypinyin 缓存]
       -> [失败仅 warning, 不阻止启动]

    -> app.setStyleSheet(GLOBAL_STYLE)                          [设置全局样式表]
    -> window = MainWindow()                                    [创建主窗口]
       -> [完整 MainWindow 初始化链见 call_graph_ui.md]
    -> window.show()                                            [显示窗口]
    -> sys.exit(app.exec())                                     [进入 Qt 事件循环]
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `main()` | `src/main.py` | Python 入口 | `setup_logging()`, `QApplication()`, `GeneralRecognizer.warmup()`, `MainWindow()` |
| `setup_logging()` | `config/logging_config.py` | `main()` | `logging.getLogger()`, `ModuleFilter`, `RotatingFileHandler` |
| `GeneralRecognizer.warmup()` | `ocr/recognizer.py` | `main()` | `self._engine`, `_load_char_info()`, `pypinyin.pinyin()` |
| `DataFacade.load_all()` | `data/manager.py` | `main()` | `HeroManager.load()`, `SynergyManager.load()`, `GuideManager.load()` |

> **启动顺序说明：** PaddleOCR 预热（约 2-3 秒）在窗口显示之前完成，避免用户看到窗口后操作卡顿。OCR 预热失败不影响启动——识别时再尝试。

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
  -> 环境变量覆盖: os.environ.get("API_KEY") → 优先于 .env
  -> 类型转换 + 默认值填充:
     -> api_key: from env or ""                                 [无默认值]
     -> api_url: from env or "https://api.deepseek.com/v1"
     -> model: from env or "deepseek-chat"
     -> requests_per_minute: from env or 30 (int)
     -> max_retries: from env or 3 (int)
     -> http_timeout: from env or 120 (int)
  -> return config dict
```

### 2.3 模拟器配置加载

```
get_mumu_config()
  -> parse_env_file(env_path)                                  [同上读取 .env]
  -> dict 构建:
     -> mumu_adb_path: from env or ""                           [ADB 路径]
     -> mumu_adb_port: from env or 7555 (int)                   [ADB 端口]
     -> mumu_ocr_enabled: from env or False (bool)              [OCR 启用]
     -> mumu_poll_mode: from env or False (bool)                [轮询模式]
     -> mumu_poll_interval: from env or 5 (int)                 [轮询间隔（秒）]
     -> mumu_ocr_match_threshold: from env or 0.8 (float)       [模板匹配阈值]
     -> mumu_generals_roi: from env or "" (解析为 tuple)        [8 个 ROI 坐标]
     -> ocr_template_path: from env or ""                       [模板文件路径]
  -> return config dict
```

### 2.4 配置文件保存

```
save_env_file(env_path, data: dict)
  -> env_path.parent.mkdir(parents=True, exist_ok=True)        [确保目录存在]
  -> lines = [f"{key}={value}\n" for key, value in data.items()]
  -> tmp_path = env_path.with_suffix(".env.tmp")               [临时文件]
  -> tmp_path.write_text("".join(lines), encoding="utf-8")     [写入 tmp]
  -> tmp_path.replace(env_path)                                [原子替换]
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `get_api_config()` | `config/env.py` | `ai_batch.py:main()`, UI 层 | `parse_env_file()`, `os.environ.get()`, 默认值 |
| `get_mumu_config()` | `config/env.py` | `MainWindow.__init__()`, `MumuConfigDialog` | `parse_env_file()`, 类型转换 |
| `parse_env_file(path)` | `config/env.py` | `get_api_config()`, `get_mumu_config()` | `Path.read_text()`, 逐行解析 |
| `load_env_config(path)` | `config/env.py` | 外部 | `parse_env_file()`, key_mapping, 类型转换 |
| `save_env_file(path, data)` | `config/env.py` | `MumuConfigDialog` 保存, `SettingsDialog` 保存 | `Path.write_text()`, 原子替换 |

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
  "src.scraper.ai_"    → logs/scraper/ai_batch.log
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
                   ← src.ui.main_window.py
                   ← src.ui.settings_dialog.py

  get_mumu_config() ← src.ui.main_window.py
                    ← src.ui.mumu_config_dialog.py

  save_env_file()   ← src.ui.main_window.py (_open_mumu_config)
                    ← src.ui.settings_dialog.py (_on_save)

  setup_logging()   ← src.main.py
                    ← 各 CLI 子进程入口 (official.py / incremental.py / ai_batch.py)
```

### 4.2 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| `src.ocr.recognizer.GeneralRecognizer.warmup()` | 预热 PaddleOCR 模型 |
| `src.data.manager.DataFacade` | 加载武将名列表 |
| `src.ui.main_window.MainWindow` | 创建主窗口 |
| `src.ui.style.GLOBAL_STYLE` | 全局样式表 |
| PaddleOCR | main() 中 warmup 创建并预热 |

---

## 五、函数清单总表

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `main()` | `src/main.py` | Python 入口 | `setup_logging()`, `QApplication()`, `GeneralRecognizer.warmup()`, `MainWindow()` |
| `setup_logging(level, file)` | `config/logging_config.py` | `main()`, 各 CLI 入口 | `RotatingFileHandler`, `ModuleFilter` |
| `get_api_config()` | `config/env.py` | `ai_batch.py`, `settings_dialog` | `parse_env_file()`, `os.environ.get()`, 默认值填充 |
| `get_mumu_config()` | `config/env.py` | `MainWindow.__init__()`, `mumu_config_dialog` | `parse_env_file()`, 类型转换 |
| `parse_env_file(path)` | `config/env.py` | `get_api_config()`, `get_mumu_config()` | `Path.read_text()`, 逐行解析 |
| `load_env_config(path)` | `config/env.py` | 外部 | `parse_env_file()`, key_mapping |
| `save_env_file(path, data)` | `config/env.py` | `MainWindow`, `SettingsDialog` | `Path.write_text()`, 原子替换 |
