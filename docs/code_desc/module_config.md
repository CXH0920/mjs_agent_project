# 模块：应用入口与配置

> 对应目录：`src/main.py` + `src/config/`
> 职责：应用启动入口、API 档案与 .env 配置管理、统一日志初始化

---

## 一、模块职责

本模块负责三件事：
1. **应用启动** — 创建 `QApplication`，在 frozen 打包模式下补齐可写运行时骨架、抑制子进程控制台弹窗，安装 Qt 中文翻译器、设置应用图标与全局样式，预热 OCR 模型，构建 `MainWindow` 并进入事件循环
2. **配置管理** — 从 API 档案（`api_profiles.json`）、`config.env` 文件、环境变量和默认值多级加载配置，维护版本控制的模型价格表
3. **统一日志** — 按模块分文件路由、10MB 轮转保留 5 份，并额外维护跨模块全量 `debug.log` 留底

---

## 二、文件结构

```
src/
├── main.py                  # 应用入口（QApplication + MainWindow 构建）
├── ui/app/app_icon.py        # 应用图标加载、缓存与顶层窗口图标维护
├── ui/app/chinese_translator.py  # Qt 标准控件中文翻译 + QMessageBox 详情按钮事件过滤器
├── config/
│   ├── __init__.py
│   ├── env.py               # .env、API 档案与模型价格配置解析/加载/保存（原子写入）
│   └── logging_config.py    # 统一日志配置（按模块拆分 + 文件轮转 + 全量留底）
```

路径常量（`env.py` 顶部，按 `IS_FROZEN` 区分）：

- `PROJECT_ROOT`：可写运行时根。开发态 = 项目根；frozen 下 = exe 所在目录，承载 `config.env` / `logs/` / 用户运行时数据
- `BUNDLE_ROOT`：只读打包资源根。开发态 = 项目根；frozen 下 = `_internal/`，承载静态数据 / 模板 / 图片 / OCR 模型
- `DEFAULT_ENV_FILE` / `DEFAULT_PRICING_FILE` / `DEFAULT_PROFILES_FILE` 分别指向 `config.env` / `_internal/config/model_pricing.json` / `config/api_profiles.json`
- `is_full_build()`：frozen 下读 `BUNDLE_ROOT/.full_build` 标记，精简版（无 RAG 维护页）由此守卫

---

## 三、核心逻辑

### 3.1 应用启动流程

```python
# src/main.py::main()
_ensure_clean_runtime()            # frozen 首启：复制 config.env.example、补齐可写目录、部署 data 静态资源
_install_no_window_patch()         # frozen 下 patch subprocess.Popen，注入 CREATE_NO_WINDOW
runtime_params = get_runtime_params()
setup_logging(log_level=..., log_to_file=...)   # 日志先于 QApplication 初始化
migrate_legacy_api_config()        # 首次启动：旧 DEEPSEEK_* 三件套 → deepseek-main 档案（幂等）

os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts=false;...")
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")    # cmd 默认 GBK，避免中文乱码（windowed 模式 stdout 可能为 None，需守卫）

QApplication.setHighDpiScaleFactorRoundingPolicy(PassThrough)
app = QApplication(sys.argv)
app.setApplicationName("名将杀 Agent")
install_chinese_qt_translator(app)
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MingJiangSha.MJSAgent")   # 任务栏图标修正
install_app_icon(app)
app.setStyleSheet(GLOBAL_STYLE)

splash = _create_startup_splash()
splash.show()
app.processEvents()
window = MainWindow()
window.start_ocr_warmup()
splash.showMessage("正在加载 OCR 模型…")
window.wait_ocr_warmup(timeout_ms=120_000)       # 覆盖 Paddle 冷加载（实测可达 90 秒+，占 GIL）
window.show()
splash.finish(window)
sys.exit(app.exec())
```

关键设计点：

1. **frozen 首启骨架补齐** — `_ensure_clean_runtime()` 仅 frozen 态执行：从 `_internal/config.env.example` 复制出 `config.env`（用户填 Key），创建 `data/` / `logs/` / `config/` / `templates/` / `images/` 目录，将 `_internal/data/` 下静态资源（核心库 json / 官方榜单 csv / RAG 语料 / 评估集 / raw_guides）递归复制到 `PROJECT_ROOT/data/`（只补缺失、不覆盖已有数据），并把元规则母本 `元规则整理-完整版.md` 部署到 `PROJECT_ROOT/docs/`（`build_rule_corpus` 等维护脚本读该路径）。开发态直接返回。
2. **抑制控制台弹窗** — `_install_no_window_patch()` 在 frozen 下给 `subprocess.Popen.__init__` 注入 `CREATE_NO_WINDOW`，避免 adb 等控制台子程序触发 Windows 新建控制台的轮询黑窗。`subprocess.run` 内部走 Popen，一并覆盖。开发态不 patch，保留控制台便于调试。
3. **OCR 预热阻塞式** — `wait_ocr_warmup(timeout_ms=120_000)` 在启动画面上完成 PaddleOCR 冷加载。因 Paddle 初始化长时间持有 Python GIL，若与主窗口事件循环并发运行会导致界面卡死，故先预热后 `window.show()`。超时覆盖 120 秒，防止冷加载超过默认值时窗口已显示但预热仍阻塞 UI。
4. **启动页异常兜底** — `MainWindow()` 或预热期间抛异常时，`splash.close()` + `logger.exception` + `QMessageBox.critical`，并 `sys.exit(1)`，避免启动页残留。
5. **frozen 重入走 runpy** — `__main__` 分支中，`IS_FROZEN and sys.argv[1]=="-m"` 时以 `runpy.run_module()` 模块模式运行（AI 攻略/相性/武将生成走 `src.scraper.ai_batch` 等 `-m` 子脚本），防止 exe 重入又拉起一个 GUI 实例。开发态 python 自带 `-m` 处理，不触发此分支。

### 3.2 配置加载优先级

```
API 档案 (api_profiles.json)  >  config.env  >  环境变量  >  默认值
```

`get_api_config()` 采用三段式解析：

```
┌─ 档案文件存在且有启用档案 ──────→ 直接返回该档案三件套
├─ 档案文件存在但无启用档案 ──────→ 仅环境变量 + 默认值兜底（刻意不读 config.env 旧键，让"停用"真正生效）
└─ 档案文件不存在（从未配置档案） ─→ 走 _legacy_api_config：config.env → 环境变量 → 默认值
```

启用互斥语义：`_normalize_profiles()` 在加载/保存时保证同时至多一个 `enabled=true`（启用即当前使用的 API）。多个 enabled 时保留第一个、其余置 false 并记 warning，数据层面即生效，避免历史文件多 enabled 导致界面显示多个启用。空 URL 不回退 DeepSeek 默认（防止跨供应商发错端点），视为无效跳过；空 Key 仅对 `requires_key=False` 的供应商（如 ollama）允许。`is_default` 字段已废弃，旧文件中的 `is_default` 被静默丢弃。

**首次启动自动迁移** — `migrate_legacy_api_config()` 在档案文件不存在且 `config.env` 中存在 `DEEPSEEK_*` 三件套（任一非空）时，自动创建名为 `deepseek-main`、`provider="deepseek"`、`enabled=True` 的默认档案，note 标注"由旧配置自动迁移"。幂等：档案文件已存在或三件套全空时不迁移。

**供应商预设表**（`PROVIDER_PRESETS`）：UI 选择 provider 时自动预填，用户可覆盖：

```python
PROVIDER_PRESETS: dict[str, dict] = {
    "deepseek":           {"api_url": "https://api.deepseek.com/v1/chat/completions", "model": "deepseek-v4-flash", "requires_key": True},
    "openai":             {"api_url": "https://api.openai.com/v1/chat/completions",     "model": "",              "requires_key": True},
    "ollama":             {"api_url": "http://localhost:11434/v1/chat/completions",     "model": "",              "requires_key": False},
    "openai-compatible":  {"api_url": "",                                                "model": "",              "requires_key": True},
}
PROVIDER_LABELS: dict[str, str] = {
    "deepseek": "DeepSeek", "openai": "OpenAI", "ollama": "Ollama", "openai-compatible": "OpenAI 兼容",
}
```

`_as_bool(value, default)` 是档案字段（如 `enabled`）的宽容布尔转换器：`None` 用 default、`bool` 原值、字符串按 `true/1/yes` 判定、其余按真值。修复旧实现 `bool(None)=False` 令 `enabled:null` 被误判为停用的问题。

### 3.3 运行时与模拟器配置

`load_env_config(env_path)` 解析 `.env` 后通过 `key_mapping` 将大写 KEY 映射为内部小写 key，并完成类型转型：整数型（`requests_per_minute` / `max_retries` / `max_output_tokens` / `http_timeout` / `mumu_adb_port` / `mumu_ocr_poll_interval` / `mumu_hero_selection_cooldown` / `mumu_ocr_cpu_threads`）、布尔型（`log_to_file` / `mumu_ocr_enabled` / `mumu_ocr_poll_mode` / `mumu_ocr_auto_switch_tab` / `mumu_ocr_use_gpu`）、浮点型（`mumu_ocr_match_threshold` / `mumu_hero_selection_threshold` / `mumu_match_guide_threshold` / `recommendation_p_floor` / `recommendation_ban_weight` / `recommendation_sigmoid_k` / `recommendation_low_win_rate_gap`）。

`get_runtime_params()` 返回：`requests_per_minute`(30) / `max_retries`(3) / `max_output_tokens`(16384) / `http_timeout`(300) / `log_level`("INFO") / `log_to_file`(True)。

`get_mumu_config()` 返回模拟器配置：`mumu_adb_path`("") / `mumu_adb_port`(0) / `mumu_ocr_enabled`(False) / `mumu_ocr_poll_mode`(False) / `mumu_ocr_auto_switch_tab`(False) / `mumu_ocr_poll_interval`(2) / `mumu_ocr_match_threshold`(0.8) / `mumu_hero_selection_threshold`(回退 match_threshold) / `mumu_hero_selection_cooldown`(180) / `mumu_match_guide_threshold`(0.8) / `mumu_ocr_use_gpu`(False) / `mumu_ocr_cpu_threads`(6)。OCR 推理配置（GPU 开关、CPU 线程数）由 `load_env_config()` 完成类型转型后提供给 `paddle_loader.create_paddle_ocr()`。

### 3.4 日志系统

`logging_config.py` 通过 `ModuleFilter(startswith=..., exclude_startswith=...)` 按 logger name 前缀分派日志到不同文件：

| logger name 前缀 | 目标文件 |
|-----------------|----------|
| `src.scraper` / `subprocess.official` | `logs/scraper/official.log` |
| `src.scraper.ai` / `subprocess.ai` | `logs/scraper/ai_generation.log` |
| `src.business.fetching` | `logs/business/fetching.log` |
| `src.business.emulator` | `logs/business/emulator.log` |
| `src.business.recognition` | `logs/business/recognition.log` |
| `src.business`（排除上述三条） | `logs/business/business.log` |
| `src.data` | `logs/data/data.log` |
| `src.ocr` | `logs/ocr/ocr.log` |
| `src.capture` | `logs/capture/capture.log` |
| `src.rag` | `logs/rag/rag.log` |
| 其他 `subprocess.*` | `logs/subprocess/unclassified.log` |
| 其他（含 `src.ui.*`） | `logs/app.log` |
| **所有（含 DEBUG）** | `logs/debug.log`（全量留底，轮转上限 2 倍） |

每条记录只进入一个业务目标文件。每个日志文件最大 10MB，保留 5 个备份自动轮转；已有旧日志不会自动删除或迁移。`debug.log` 跨模块全量留底，`maxBytes` 为 2 倍、轮转备份数为 `max(log_backup_count, 1)`，级别恒为 DEBUG。

handler 分两类：
- **keep_debug=True 文件**（`official.log` / `ai_generation.log` / `unclassified.log`）承载子进程 stdout/stderr 转发流，handler 级别固定 DEBUG，不跟随用户级别，保证子进程原始输出在 WARNING 模式下也不丢失。
- **其余 src 路由文件**跟随用户级别，由 handler 层裁剪。

root 级别上限 WARNING（`root.setLevel(max(level, logging.WARNING))`）：第三方库（chromadb / transformers 等）作为 root 的直接子且级别 NOTSET，继承 root 后其 INFO/DEBUG 在 logger 层即被挡、不创建 LogRecord，实现零库名清单的高效压制。项目 `src` / `subprocess` 前缀单独设 `DEBUG` 全量创建，供 `debug.log` 留底与子进程输出转发。

**子进程日志策略**：由桌面应用启动的 QProcess 子进程设置 `MJS_QPROCESS_CHILD=1` 后跳过所有文件 Handler，仅输出控制台，stdout/stderr 由父进程统一收集并路由到对应日志文件，避免多进程同时轮转同一组文件导致 Windows 文件占用与备份竞争。`src.scraper.ai` / `subprocess.ai` 命名空间对应的 `ai_generation.log` handler 采用 `keep_debug=True` + 独立路由设计，使子进程原始输出（含 429 / length / JSON 等失败原因）在 root WARNING 下也不丢失。

### 3.5 模型价格

- `config/model_pricing.json`（frozen 下位于 `BUNDLE_ROOT/config/`）是版本控制的模型价格来源。未知模型不会套用默认价格，而是返回"无法自动估算"（`get_model_pricing` 返回 `None`）。
- 价格文件包含 `currency`、`unit`、`updated_at` 和 `models`；计价单位为"百万tokens"，每个模型维护 `input_per_million`、`output_per_million`、可选 `cached_input_per_million` 单价。
- `load_pricing_config(path)` 负责读取价格表，文件不存在或格式无效时返回默认空表 `{"currency":"CNY","unit":"百万tokens","updated_at":"","models":{}}`。
- `save_pricing_config(path, data)` 负责 UTF-8 无 BOM、LF 换行的原子写入。
- `get_model_pricing(model)` 校验单价必须为非负数字（且不是 bool），非法返回 `None`。
- API 配置对话框的"价格配置"页签直接维护该文件，保存前会校验模型名称唯一且单价为非负数。

---

## 四、关键代码片段

### 4.1 原子写入 .env（保留注释与无关键）

```python
def save_env_file(env_path, data):
    # 1. 读原文件：保留空行/注释行，收集既有 key 集合
    existing_keys = set()
    lines = []
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(line)                       # 保留注释/空行
            else:
                key = stripped.split("=")[0].strip() if "=" in stripped else ""
                if key not in data:
                    lines.append(line)                   # 保留无关旧 key
                if key:
                    existing_keys.add(key)
    # 2. 追加新 key；3. 原地更新既有 key；4. .tmp → replace 原子覆盖
    tmp_path = env_path.with_suffix(".env.tmp")
    tmp_path.write_text("\n".join(result_lines) + "\n", encoding="utf-8")
    tmp_path.replace(env_path)
```

> **设计思路：** 直接写原文件如果中途崩溃会导致 .env 半损坏。`.tmp` → `.replace()` 在 NTFS 上是原子操作，写入成功前原文件不变。保留注释与无关键避免用户手写配置被覆盖丢失；若旧文件读失败则直接 `raise`，而非用空内容静默覆盖。

### 4.2 日志路由的幂等性与根级别倒置

```python
def setup_logging(log_level=DEFAULT_LEVEL, log_to_file=True, ...):
    root = logging.getLogger()
    # 只清理本模块之前创建的 Handler，保留 pytest/宿主等外部 Handler
    for handler in root.handlers[:]:
        if getattr(handler, _MANAGED_HANDLER_ATTR, False):
            root.removeHandler(handler)
            handler.close()
    # root 下限 WARNING：压制第三方库 INFO/DEBUG
    root.setLevel(max(level, logging.WARNING))
    ...
    # 反转级别：项目 src/subprocess 前缀恒定 DEBUG 全量创建，成全 debug.log 留底
    logging.getLogger("src").setLevel(logging.DEBUG)
    logging.getLogger("subprocess").setLevel(logging.DEBUG)
```

> **设计思路：** 只清理项目自身创建的 Handler，避免重复注册，同时不破坏外部日志 Handler。root 下限 WARNING + `src`/`subprocess` 单独设 DEBUG 形成"倒置"——第三方库静默、项目全量留底，`debug.log` 用 2 倍轮转上限承载。

### 4.3 API 档案归一化与启用互斥

```python
def _normalize_profiles(profiles) -> list[dict]:
    result, seen = [], set()
    enabled_seen = False
    for i, raw in enumerate(profiles):
        ...
        enabled = _as_bool(raw.get("enabled"), True)
        if enabled:
            if enabled_seen:                               # 第二个启用的→强制停用
                logger.warning("存在多个启用的 API 档案，仅保留第一个，其余停用: %s", name)
                enabled = False
            else:
                enabled_seen = True
        result.append({"name": name, "provider": ..., "enabled": enabled, ...})
    return result
```

> **设计思路：** 启用互斥在数据层面保证（同时至多一个 enabled=true），打开配置即生效，避免历史文件多 enabled 导致界面显示多个启用的漂移。名称重复时自动追加 `-2/-3` 后缀。

---

## 五、接口说明

本模块不提供外部 API 接口，但提供以下公共函数（均经 `src.config.__init__.py` 导出）：

| 函数 / 常量 | 文件 | 返回 | 说明 |
|-------------|------|------|------|
| `IS_FROZEN` | `env.py` | `bool` | 是否 PyInstaller 打包态 |
| `PROJECT_ROOT` / `BUNDLE_ROOT` | `env.py` | `Path` | 可写运行时根 / 只读打包资源根 |
| `DEFAULT_ENV_FILE` / `DEFAULT_PRICING_FILE` / `DEFAULT_PROFILES_FILE` | `env.py` | `Path` | 各配置文件默认路径 |
| `DEFAULT_API_URL` / `DEFAULT_MODEL` | `env.py` | `str` | DeepSeek 默认 API 端点与模型 |
| `PROVIDER_PRESETS` / `PROVIDER_LABELS` | `env.py` | `dict` | 供应商预设（URL/默认模型/是否需要 Key）与展示名 |
| `is_full_build()` | `env.py` | `bool` | frozen 下 `.full_build` 标记是否存在 |
| `parse_env_file(path)` | `env.py` | `dict[str, str]` | 解析 .env 文件（含注释、引号处理） |
| `load_env_config(path)` | `env.py` | `dict` | 解析并完成类型转型（整数/布尔/浮点） |
| `get_api_config()` | `env.py` | `dict` | 三段式获取 API 配置（档案 → 环境变量 → 旧链 → 默认值） |
| `get_runtime_params()` | `env.py` | `dict` | 运行参数：RPM / 重试 / 超时 / 日志等级 / log_to_file |
| `get_mumu_config()` | `env.py` | `dict` | 模拟器配置（ADB / OCR / 阈值 / 冷却） |
| `save_env_file(path, data)` | `env.py` | `None` | 原子写入 .env（保留注释与无关键） |
| `load_pricing_config(path)` | `env.py` | `dict` | 加载模型价格配置 |
| `save_pricing_config(path, data)` | `env.py` | `None` | 原子保存模型价格配置 |
| `get_model_pricing(model)` | `env.py` | `dict \| None` | 查询模型单价，未知模型返回 `None` |
| `load_api_profiles(path)` | `env.py` | `dict` | 读取 API 档案（含归一化与启用互斥） |
| `save_api_profiles(data, path)` | `env.py` | `None` | 原子保存 API 档案（空档案自动删文件回到旧链兜底） |
| `list_api_profiles()` | `env.py` | `list[dict]` | 供 UI 展示，api_key 以 `has_key` 布尔代替，不回显明文 |
| `get_api_profile(name)` | `env.py` | `dict \| None` | 按名称取完整档案（含 api_key），仅供任务解析，不入日志/UI |
| `resolve_api_config(name)` | `env.py` | `dict` | 任务侧唯一 API 解析入口：指定档案 → 默认解析 |
| `has_available_api_profile()` | `env.py` | `bool` | 是否存在可用（enabled+URL 非空+供应商 Key 语义）的档案 |
| `migrate_legacy_api_config(env_path, profiles_path)` | `env.py` | `bool` | 旧 DEEPSEEK_* 三件套 → deepseek-main 档案（幂等） |
| `setup_logging(...)` | `logging_config.py` | `None` | 初始化日志系统（幂等，只清理自身 Handler） |
| `install_chinese_qt_translator(app)` | `ui/app/chinese_translator.py` | `ChineseQtTranslator` | 安装应用级 Qt 标准控件中文翻译器 |
| `install_details_button_translator(msgbox)` | `ui/app/chinese_translator.py` | `_DetailsButtonFilter` | 为 QMessageBox 安装详情按钮翻译过滤器（"查看详情/隐藏详情"），随对话框销毁；Qt 内部展开/收起时直接 setText 不走 QTranslator，需在 layout 变化时遍历子按钮翻译 |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 被依赖 | — | 所有模块都通过 `from src.config.env import ...` 获取配置 |
| 被依赖 | `src.rag.config` | RAG 语料/向量索引与预算配置（RAG_ENABLED / RAG_TOP_K / RAG_PROMPT_CHARS / RAG_BROWSER_PROMPT_CHARS / RAG_SYNERGY_PROMPT_CHARS / RAG_MODEL_DIR），由 AI 批量生成模块使用 |
| 被依赖 | `src.ui.app` / `src.scraper.ai` | 通过 `get_api_config()` / `resolve_api_config(name)` / `list_api_profiles()` 获取 API 三件套 |
| 被依赖 | `src.ui.shared.style` | `main.py` 通过 `GLOBAL_STYLE` 设置全局样式 |
| 被依赖 | `src.ui.app.app_icon` | `main.py` 通过 `install_app_icon()` 设置应用图标 |
| 被依赖 | `src.config.logging_config` | 所有模块共享同一日志系统，QProcess 子进程通过 `MJS_QPROCESS_CHILD=1` 信号仅输出控制台 |
| 依赖 | `src.ui.app.main_window` | 应用入口创建 MainWindow 实例并调用 `start_ocr_warmup()` / `wait_ocr_warmup()` |
| 依赖 | `src.business.recognition.ocr_worker` | 预热 PaddleOCR 模型 |
| 依赖 | `runpy`（frozen 重入） | `-m` 子脚本以模块模式运行，避免 exe 重入拉起 GUI |