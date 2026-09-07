# 调用链路：应用入口与配置

> 对应源码：`src/main.py` + `src/config/`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。

---

## 当前实现基线（2026-09-07）

`get_api_config()` 优先级为 `config/api_profiles.json（可用档案）> 仅环境变量 > config.env 旧链 > 默认值`；默认 API 地址为 `https://api.deepseek.com/v1/chat/completions`，默认模型为 `deepseek-v4-flash`。任务侧统一经 `resolve_api_config(name)` 解析，可用性判定统一经 `_usable_profile_config()`。`get_runtime_params()` 和 `get_mumu_config()` 经 `load_env_config()` 完成字段映射与类型转换。

```
main() -> get_runtime_params() -> setup_logging()
main() -> migrate_legacy_api_config()
AppServices.__init__() -> get_mumu_config() -> CaptureService/OcrService.update_config()
ai_batch.main() -> resolve_api_config(None)
```

`save_env_file()` 先保留原文件注释和未修改键，再写入 `.env.tmp` 并 `replace()` 原子替换。启动阶段 `main()` 在启动页显示期间创建 `MainWindow`，随即 `start_ocr_warmup()` 并以 `wait_ocr_warmup(timeout_ms=120_000)` 阻塞完成 PaddleOCR 冷加载，之后再 `window.show()`——Paddle 初始化长时间持有 GIL，预热不能与事件循环并发。日志侧已无 AI 子进程直写文件的例外：所有 QProcess 子进程仅输出控制台，由父进程转发落盘。

## 一、应用启动链路

### 1.1 主函数完整调用链

```
[操作系统] python src/main.py  /  mjs_agent.exe [-m <module> 子脚本]
  -> main()                                                    [应用入口]
    -> _ensure_clean_runtime()                                 [frozen 首启骨架补齐；开发态直接返回]
       -> shutil.copy2(BUNDLE_ROOT/config.env.example -> PROJECT_ROOT/config.env)
       -> shutil.copy2(BUNDLE_ROOT/config/ocr_rois.default.json -> config/ocr_rois.json) [缺失才复制]
       -> mkdir: data/ logs/ config/ templates/ images/
       -> rglob 复制 BUNDLE_ROOT/data/** 静态资源（只补缺失、不覆盖）
       -> shutil.copy2(BUNDLE_ROOT/docs/元规则整理-完整版.md -> docs/)
    -> _install_no_window_patch()                              [frozen 下 patch subprocess.Popen 注入 CREATE_NO_WINDOW]
    -> runtime_params = get_runtime_params()                   [运行参数：RPM/重试/token/超时/日志等级]
    -> setup_logging(log_level=..., log_to_file=...)           [日志初始化（幂等，先于 QApplication）]
       -> [详见 §3.1]
    -> migrate_legacy_api_config()                             [首次启动迁移：旧 DEEPSEEK_* 三件套 -> deepseek-main 档案]
       -> [档案文件已存在 或 三件套全空] return False           [幂等]
       -> parse_env_file() -> save_api_profiles(...)
    -> os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts=false;qt.text.font.db=false")
       [抑制 Qt 字体回退日志]
    -> [win32] sys.stdout/sys.stderr.reconfigure(encoding="utf-8")
       [cmd 默认 GBK；windowed 模式为 None 需守卫]
    -> QApplication.setHighDpiScaleFactorRoundingPolicy(PassThrough)
    -> app = QApplication(sys.argv)                            [创建 Qt 应用]
    -> app.setApplicationName("名将杀 Agent") / setOrganizationName("MingJiangSha") / setApplicationVersion("0.1.0")
    -> install_chinese_qt_translator(app)                      [Qt 标准控件中文翻译器]
    -> [win32] ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MingJiangSha.MJSAgent")
       [修正 Windows 任务栏图标, 静默失败]
    -> install_app_icon(app)                                   [加载/缓存图标并安装恢复器]
       -> load_app_icon()                                      [基于源码绝对路径加载 mjs.ico]
       -> app.setWindowIcon(icon)                              [设置应用默认图标]
       -> app.installEventFilter(_AppIconKeeper)               [窗口显示/激活时恢复图标]
    -> app.setStyleSheet(GLOBAL_STYLE)                         [设置全局样式表]
    -> splash = _create_startup_splash(); splash.show(); app.processEvents()
    -> window = MainWindow()                                   [创建主窗口]
       -> [完整 MainWindow 初始化链见 call_graph_ui.md]
       -> AppServices.__init__() -> get_mumu_config() -> CaptureService/OcrService.update_config()
    -> window.start_ocr_warmup()                               [启动 OCR 预热，不依赖模拟器连接]
       -> CaptureService.warmup_ocr_model(hero_names) -> OcrWorker.warmup_model()
    -> splash.showMessage("正在加载 OCR 模型…"); app.processEvents()
    -> window.wait_ocr_warmup(timeout_ms=120_000)              [阻塞至冷加载完成，超时则继续显示]
       -> CaptureService.wait_ocr_warmup() -> warmup_task.completed.wait()
    -> window.show()                                           [显示窗口]
    -> splash.finish(window)
    -> sys.exit(app.exec())                                    [进入 Qt 事件循环]
    -> [异常兜底] splash.close() + logger.exception + QMessageBox.critical + sys.exit(1)
```

入口守卫（`__main__` 分支）：`IS_FROZEN and sys.argv[1] == "-m"` 时以 `runpy.run_module()` 模块模式运行（AI 攻略/相性/武将生成走 `src.scraper.ai_batch` 等 `-m` 子脚本），避免 exe 重入又拉起一个 GUI 实例；开发态 python 自带 `-m` 处理，不触发此分支。

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `main()` | `src/main.py` | Python 入口 / exe | `_ensure_clean_runtime()`, `_install_no_window_patch()`, `get_runtime_params()`, `setup_logging()`, `migrate_legacy_api_config()`, `QApplication()`, `install_chinese_qt_translator()`, `install_app_icon()`, `MainWindow()` |
| `setup_logging()` | `config/logging_config.py` | `main()`, 各 CLI 入口 | 见 §3.1 |
| `MainWindow.__init__()` | `ui/app/main_window.py` | `main()` | `AppServices` 组合根、`_load_data()`, `_setup_ui()`, `_setup_status_bar()`, `_update_status()` |
| `MainWindow.start_ocr_warmup()` / `wait_ocr_warmup()` | `ui/app/main_window.py` | `main()` | `CaptureService.warmup_ocr_model()` / `CaptureService.wait_ocr_warmup()` |
| `DataFacade.load_all()` | `data/manager.py` | `MainWindow._load_data()` | `HeroManager.load()`, `SynergyManager.load()`, `GuideManager.load()` |

> **启动顺序说明：** `main()` 只做骨架、日志、Qt 外壳与窗口创建；模拟器的 ADB/OCR 配置经 `AppServices` 组合根注入，OCR 模型冷加载在启动页阶段阻塞完成。这样窗口显示后事件循环保持流畅，模型加载成本不落入交互期。

---

## 二、配置加载链路

### 2.1 配置加载优先级

```
API 档案 (config/api_profiles.json) > config.env > 环境变量 > 默认值
```

档案文件存在但无可用档案时**刻意跳过** `config.env` 旧键，仅用环境变量 + 默认值兜底，使"停用/删光档案"真正生效，避免旧 Key 静默回跑。

### 2.2 API 配置加载

```
get_api_config()
  -> load_api_profiles()                                      [读取 config/api_profiles.json]
     -> [文件不存在/损坏] return {"version": 1, "profiles": []}
     -> _normalize_profiles(profiles)                          [跳过非法项、补默认字段、名称去重、启用互斥]
        -> _as_bool(raw["enabled"], default=True)              [null 不误判为停用]
  -> 遍历档案 -> _usable_profile_config(profile)              [可用性唯一判定，三条路径共用]
     -> [enabled=false 或 api_url 为空] return None            [空 URL 不回退 DeepSeek 默认，避免跨供应商发错端点]
     -> [供应商 requires_key=True 且 api_key 为空] return None [仅 ollama 等 requires_key=False 允许空 Key]
     -> return {"provider", "api_key", "api_url", "model"}     [model 为空则回退 deepseek-v4-flash]
  -> [有可用档案] return 首个可用档案三件套                    [启用互斥：同时至多一个 enabled=true]
  -> [档案文件存在但无可用档案] return _env_var_fallback()     [仅环境变量 + 默认值]
     -> api_key: DEEPSEEK_API_KEY -> OPENAI_API_KEY -> ""
     -> api_url: DEEPSEEK_API_URL -> "https://api.deepseek.com/v1/chat/completions"
     -> model: DEEPSEEK_MODEL -> "deepseek-v4-flash"
  -> [档案文件不存在（从未配置）] return _legacy_api_config()  [config.env -> 环境变量 -> 默认值]
     -> config = load_env_config() -> parse_env_file()
     -> api_key: config.env DEEPSEEK_API_KEY -> DEEPSEEK_API_KEY -> OPENAI_API_KEY -> ""
     -> api_url: config.env DEEPSEEK_API_URL -> 默认
     -> model: config.env DEEPSEEK_MODEL -> 默认

resolve_api_config(name=None)                                 [任务侧唯一 API 解析入口]
  -> [name 非空] get_api_profile(name) -> _usable_profile_config()
  -> [不存在/停用] logger.warning 并回退
  -> get_api_config()

has_available_api_profile()                                   [对话框与生成链路共用]
  -> load_api_profiles() -> 逐档案 _usable_profile_config()
  -> [任一可用] True

migrate_legacy_api_config()                                   [main() 首启调用，幂等]
  -> [api_profiles.json 已存在] return False
  -> parse_env_file() 取 DEEPSEEK_API_KEY / DEEPSEEK_API_URL / DEEPSEEK_MODEL
  -> [三件套全空] return False
  -> save_api_profiles({name: "deepseek-main", provider: "deepseek", enabled: true, note: "由旧配置自动迁移"})
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
     -> mumu_ocr_auto_switch_tab: from env or False (bool)      [轮询时自动切回游戏标签]
     -> mumu_ocr_poll_interval: from env or 2 (int)             [轮询间隔（秒）]
     -> mumu_ocr_match_threshold: from env or 0.8 (float)       [兼容旧版通用阈值]
     -> mumu_hero_selection_threshold: from env or 0.8 (float)  [选将模板阈值，回退 match_threshold]
     -> mumu_hero_selection_cooldown: from env or 180 (int)     [选将冷却秒数]
     -> mumu_match_guide_threshold: from env or 0.8 (float)     [对局攻略模板阈值]
     -> mumu_ocr_use_gpu: from env or False (bool)              [推理设备开关]
     -> mumu_ocr_cpu_threads: from env or 6 (int)               [CPU 推理线程上限]
  -> return config dict
```

消费方：`AppServices.__init__()` 注入 `CaptureService` / `OcrService.update_config()`；`paddle_loader.create_paddle_ocr()` 读 `mumu_ocr_use_gpu` / `mumu_ocr_cpu_threads` 决定推理设备；`MainWindow._open_mumu_config()` 以同一份字典为对话框数据源并回写 `config.env`。

### 2.4 配置文件保存

```
save_env_file(env_path, data)
  -> 读取原文件并保留注释/未修改键（读失败直接 raise，不用空内容覆盖用户配置）
  -> 合并 data 中的新值或覆盖已有键
  -> tmp_path = env_path.with_suffix(".env.tmp")               [临时文件]
  -> tmp_path.write_text("\\n".join(lines) + "\\n", encoding="utf-8") [UTF-8 写入 tmp]
  -> tmp_path.replace(env_path)                                [原子替换]

save_api_profiles(data, profiles_path)
  -> _normalize_profiles(data["profiles"])                     [保存前归一化：启用互斥/名称去重]
  -> [profiles 为空] 不写空文件（已存在则 unlink），回到"从未配置档案"状态
     [避免空文件被误判为"仍在档案体系"导致旧 Key 静默旁路]
  -> json.dumps(ensure_ascii=False, indent=2) -> *.tmp -> replace(path)
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `get_api_config()` | `config/env.py` | `resolve_api_config()`, `business/ai_cost.py`, `scripts/run_synergy_drift.py` | `load_api_profiles()`, `_usable_profile_config()`, `_env_var_fallback()`, `_legacy_api_config()` |
| `resolve_api_config(name)` | `config/env.py` | `scraper/ai/batch.py`, `business/rag/refinement_service.py` | `get_api_profile()`, `_usable_profile_config()`, `get_api_config()` |
| `has_available_api_profile()` | `config/env.py` | `ui/generation/backend_choose_dialog.py` | `load_api_profiles()`, `_usable_profile_config()` |
| `get_mumu_config()` | `config/env.py` | `ui/app/app_services.py`, `ui/app/main_window.py`, `ocr/paddle_loader.py` | `load_env_config()` |
| `parse_env_file(path)` | `config/env.py` | `load_env_config()`, `load_api_profiles` 迁移链 | `Path.read_text()`, 逐行解析 |
| `load_env_config(path)` | `config/env.py` | `get_runtime_params()`, `get_mumu_config()`, `_legacy_api_config()`, `rag/config.py` | `parse_env_file()`, key_mapping, 类型转换 |
| `save_env_file(path, data)` | `config/env.py` | `ui/app/main_window.py` (_open_mumu_config), `ui/configuration/settings_dialog.py` (_on_save) | `Path.write_text()`, 原子替换 |
| `migrate_legacy_api_config()` | `config/env.py` | `src/main.py::main()` | `parse_env_file()`, `save_api_profiles()` |

### 2.5 模型价格配置

成本估算读取独立 JSON，不混入 `config.env`：

```
business/ai_cost.estimate_generation_cost(items, kind, model=None, use_rag=None)
  -> [model 缺省] get_api_config()["model"]                    [取当前生效档案的模型]
  -> scraper/ai/prompt_utils.estimate_cost / estimate_item_cost
     -> get_model_pricing(model)
        -> load_pricing_config(config/model_pricing.json)
        -> 校验 input_per_million / output_per_million（非负数字，bool 视为非法）
        -> [未知模型或非法配置] return None → 调用方提示"无法自动估算"
  -> 计算输入/输出 token 与费用

ui/configuration/settings_dialog.py ("价格配置"页签)
  -> load_pricing_config(path) / save_pricing_config(path, data)
     -> json.dumps(ensure_ascii=False, indent=2)
     -> *.tmp.write_text(encoding="utf-8", newline="\\n")
     -> replace(path)
```

| 函数 | 文件 | 说明 |
|------|------|------|
| `load_pricing_config(path)` | `config/env.py` | 读取并校验价格 JSON，异常时返回空模型表 |
| `get_model_pricing(model)` | `config/env.py` | 返回指定模型单价，未知或非法配置返回 `None` |
| `save_pricing_config(path, data)` | `config/env.py` | UTF-8、LF、无 BOM 原子写入 |

### 2.6 RAG 语料配置（src/rag/config.py）

RAG 检索配置独立于 `src/config/env.py`，位于 `src/rag/config.py`，遵循「环境变量 > config.env > 默认值」：

```
RAG_ENABLED                # 默认 true；--no-rag 时由 ai/batch.py 将环境变量置为 false
RAG_TOP_K                  # 检索结果上限，默认 12
RAG_PROMPT_CHARS           # 攻略 RAG 注入字符预算，默认 6000
RAG_BROWSER_PROMPT_CHARS   # 浏览器模式攻略预算，默认 3000
RAG_SYNERGY_PROMPT_CHARS   # 相性 RAG 注入字符预算，默认 6000
RAG_MODEL_DIR              # 本地 bge-small-zh-v1.5 嵌入模型缓存目录
RAG_PROJECT_DIR            # 预留的 RAG 项目目录（兼容旧配置）
```

调用方：`src/scraper/ai/rag_prompt.py`（开关与注入预算）、`src/rag/retriever.py`（检索参数）、`src/rag/indexer.py`（语料与索引路径）。详见 AI 批量生成模块文档。

---

## 三、日志系统链路

### 3.1 日志初始化

```
setup_logging(log_level="INFO", log_to_file=True, log_max_mb=10, log_backup_count=5)
  -> level = LEVEL_MAP.get(str(log_level).upper(), logging.INFO)   [DEBUG/INFO/WARNING/ERROR]
  -> root = logging.getLogger()
  -> 仅移除带 _mjs_managed_handler 标记的旧 Handler                [保留 pytest/宿主等外部 Handler]
  -> root.setLevel(max(level, logging.WARNING))                   [下限 WARNING：零库名清单压制第三方库]
  -> [控制台] logging.StreamHandler(sys.stdout)，级别=用户级别
  -> is_qprocess_child = (MJS_QPROCESS_CHILD == "1")
  -> [not log_to_file 或 is_qprocess_child] return                [跳过文件 Handler，含全部 AI 子进程]
  -> [log_to_file 且非 QProcess 子进程]
     -> max_bytes = max(log_max_mb, 1) * 1024 * 1024
     -> [每日志分类] 建目录 + RotatingFileHandler(maxBytes, backupCount, encoding="utf-8")
     -> ModuleFilter(startswith=..., exclude_startswith=...)      [按 logger name 前缀匹配]
     -> handler.setLevel(DEBUG if keep_debug else 用户级别)
  -> [留底] RotatingFileHandler("logs/debug.log", maxBytes=max_bytes*2,
                               backupCount=max(log_backup_count,1), level=DEBUG)
  -> logging.getLogger("src").setLevel(DEBUG)                     [反转级别：项目前缀全量创建]
  -> logging.getLogger("subprocess").setLevel(DEBUG)
```

> **子进程日志保留**：所有 QProcess 子进程（`MJS_QPROCESS_CHILD=1`，含 AI 子进程）都不直写文件，stdout/stderr 由父进程统一收集后转发落盘，消除 AI 子进程与 GUI 父进程同写 `ai_generation.log` 导致的 Windows RotatingFileHandler 轮转 rename 竞争。`scraper/ai_generation.log` handler 设 `keep_debug=True`（级别固定 DEBUG、不跟随用户级别），因此 root ≥WARNING 时子进程转发的 429/length/JSON 失败原因仍被保留；另有 `debug.log` 跨模块全量留底（DEBUG、无前缀过滤、单独 2 倍轮转上限）。

```
日志分发规则:
  logger name 前缀 → 目标文件:
  "src.scraper" / "subprocess.official" → logs/scraper/official.log        [排除 src.scraper.ai]
  "src.scraper.ai" / "subprocess.ai"    → logs/scraper/ai_generation.log
  "src.business.fetching"               → logs/business/fetching.log
  "src.business.emulator"               → logs/business/emulator.log
  "src.business.recognition"            → logs/business/recognition.log
  "src.business"（排除上述三条）          → logs/business/business.log
  "src.data" / "src.ocr" / "src.capture" / "src.rag" → 各自同名日志
  其他 "subprocess.*"                   → logs/subprocess/unclassified.log  [排除 official/ai]
  (其他，含 "src.ui.*")                 → logs/app.log                       [排除上列全部业务前缀]
  所有（无前缀过滤，level=DEBUG）        → logs/debug.log
```

| 函数 | 所在文件 | 说明 |
|------|----------|------|
| `setup_logging(log_level, log_to_file, log_max_mb, log_backup_count)` | `config/logging_config.py` | 初始化日志系统（幂等，只清理自身 Handler） |
| `ModuleFilter(startswith, exclude_startswith)` | `config/logging_config.py` | 自定义 Filter: logger.name 前缀匹配/排除 |

---

## 四、外部调用关系总览

### 4.1 本模块被外部调用

```
src.main.py 是桌面应用的唯一直接执行入口（frozen 下 -m 子脚本经 runpy 重入同一 exe）。

src.config.env 的消费方:
  get_api_config()              ← src.business.ai_cost.py（成本估算取当前生效模型）
                                ← src.scripts.run_synergy_drift.py
                                ← resolve_api_config() 内部兜底
  resolve_api_config(name)      ← src.scraper.ai.batch.py（任务侧唯一入口）
                                ← src.business.rag.refinement_service.py
  has_available_api_profile()   ← src.ui.generation.backend_choose_dialog.py
  load_api_profiles()           ← src.ui.configuration.settings_dialog.py
  save_api_profiles()           ← src.ui.configuration.settings_dialog.py
  list_api_profiles()           ← tests/test_api_profiles.py（当前无 UI 消费方）
  get_mumu_config()             ← src.ui.app.app_services.py（注入 Capture/Ocr 服务）
                                ← src.ui.app.main_window.py（_open_mumu_config）
                                ← src.ocr.paddle_loader.py（推理设备 / CPU 线程）
  save_env_file()               ← src.ui.app.main_window.py（_open_mumu_config）
                                ← src.ui.configuration.settings_dialog.py（_on_save）
  load_pricing_config()         ← src.ui.configuration.settings_dialog.py（"价格配置"页签）
  save_pricing_config()         ← src.ui.configuration.settings_dialog.py（_on_save）
  get_model_pricing()           ← src.scraper.ai.prompt_utils.py（estimate_cost / estimate_item_cost）
  migrate_legacy_api_config()   ← src.main.py::main()（首启幂等迁移）
  get_runtime_params()          ← src.main.py::main()
  setup_logging()               ← src.main.py
                                ← src.scraper.official_source.full.py / incremental.py
                                ← src.scraper.ai.batch.py
                                ← src.rag.indexer.py
```

### 4.2 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| `src.ui.app.main_window.MainWindow` | 创建主窗口，并触发 `start_ocr_warmup()` / `wait_ocr_warmup()` |
| `src.ui.app.app_icon.install_app_icon` | 加载/缓存图标并安装图标恢复器 |
| `src.ui.app.chinese_translator.install_chinese_qt_translator` | Qt 标准控件中文翻译器 |
| `src.ui.shared.style.GLOBAL_STYLE` | 全局样式表 |
| `src.business.emulator.capture_service` → `src.business.recognition.ocr_worker` → `src.ocr.paddle_loader` | 启动页阶段完成 PaddleOCR 冷加载（传递依赖，主入口不直接引用） |
| `runpy` | frozen 重入下以模块模式运行 `-m` 子脚本 |

---

## 五、函数清单总表

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `main()` | `src/main.py` | Python 入口 / exe | `_ensure_clean_runtime()`, `_install_no_window_patch()`, `get_runtime_params()`, `setup_logging()`, `migrate_legacy_api_config()`, `QApplication()`, `install_chinese_qt_translator()`, `install_app_icon()`, `MainWindow()` |
| `setup_logging(level, file, max_mb, backup_count)` | `config/logging_config.py` | `main()`, 各 CLI 入口 | `RotatingFileHandler`, `ModuleFilter`；root 下限 WARNING，`src`/`subprocess` 恒 DEBUG，`keep_debug=True` 保留子进程原始输出，`MJS_QPROCESS_CHILD=1` 跳过文件 Handler |
| `install_chinese_qt_translator(app)` | `ui/app/chinese_translator.py` | `main()` | 安装 Qt 标准控件中文翻译器 |
| `install_details_button_translator(msgbox)` | `ui/app/chinese_translator.py` | `AiGenerationWorkflow._on_guide_error()` / `_on_synergy_error()` | 为 QMessageBox 安装详情按钮翻译过滤器（"查看详情/隐藏详情"） |
| `get_api_config()` | `config/env.py` | `resolve_api_config()`, `ai_cost`, `run_synergy_drift` | `load_api_profiles()`, `_usable_profile_config()`, `_env_var_fallback()`, `_legacy_api_config()` |
| `resolve_api_config(name)` | `config/env.py` | `ai/batch.py`, `rag/refinement_service` | `get_api_profile()`, `_usable_profile_config()`, `get_api_config()` |
| `has_available_api_profile()` | `config/env.py` | `backend_choose_dialog` | `load_api_profiles()`, `_usable_profile_config()` |
| `load_api_profiles()` / `save_api_profiles()` | `config/env.py` | `settings_dialog`, `_normalize_profiles()` | 归一化（启用互斥/名称去重）+ UTF-8、LF、原子替换 |
| `get_mumu_config()` | `config/env.py` | `app_services`, `main_window`, `paddle_loader` | `load_env_config()`, 类型转换 |
| `get_runtime_params()` | `config/env.py` | `main()`, 各 CLI 入口 | `load_env_config()` |
| `parse_env_file(path)` | `config/env.py` | `load_env_config()`, 迁移链 | `Path.read_text()`, 逐行解析 |
| `load_env_config(path)` | `config/env.py` | `get_runtime_params()`, `get_mumu_config()`, `_legacy_api_config()`, `rag/config.py` | `parse_env_file()`, key_mapping |
| `save_env_file(path, data)` | `config/env.py` | `main_window`, `settings_dialog` | `Path.write_text()`, 原子替换 |
| `migrate_legacy_api_config()` | `config/env.py` | `main()` | `parse_env_file()`, `save_api_profiles()` |
| `load_pricing_config()` / `save_pricing_config()` / `get_model_pricing()` | `config/env.py` | `settings_dialog`, `prompt_utils` | 价格表校验 + 原子替换 |
