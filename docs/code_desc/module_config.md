# 模块：应用入口与配置

> 对应目录：`src/main.py` + `src/config/`
> 职责：应用启动入口、API 档案与 .env 配置管理、版本单一来源、统一日志初始化
> 文档日期：2026-10-23（基线 a63bc3c）

---

## 一、模块职责

本模块负责三件事：
1. **应用启动** — 创建 `QApplication`，在 frozen 打包模式下补齐可写运行时骨架（`data/.deployed` 部署标记跳过重复扫描）、抑制子进程控制台弹窗，安装 Qt 中文翻译器、设置应用图标与全局样式；免责声明确认通过后，在启动页上构建 `MainWindow` 并等待 OCR 引擎冷加载完成，随后进入事件循环；首启向导（环境自检→模拟器连接→试识别）在窗口显示后按状态文件判定弹一次，跳过或完成后不再打扰；另承担启动期轻运维：截图目录回收（14 天）与每日一次的自动公告检查
2. **配置管理** — 以多 API 档案（`api_profiles.json`，启用互斥）为首选来源，回退 `config.env` 文件、环境变量与默认值多级加载配置；维护版本控制的模型价格表；旧 `DEEPSEEK_*` 三件套首启自动迁移为默认档案；版本号单一来源 `version.py::app_version()`（读根目录 `VERSION` 文件，当前 1.2.1）
3. **统一日志** — 按模块分文件路由、10MB 轮转保留 5 份，并额外维护跨模块全量 `debug.log` 留底；注册 `threading.excepthook` 与链式 `sys.excepthook` 兜底记录线程/逃逸异常

---

## 二、文件结构

```
src/
├── main.py                  # 应用入口（QApplication + MainWindow 构建）
├── ui/app/app_icon.py        # 应用图标加载、缓存与顶层窗口图标维护
├── ui/app/chinese_translator.py  # Qt 标准控件中文翻译 + QMessageBox 详情按钮事件过滤器
├── ui/app/onboarding_dialog.py   # 首启向导：三段一页式体检（环境自检→模拟器连接→试识别）
├── ui/app/onboarding_fixes.py    # doctor 修复文案→向导动作映射（一键修复按钮文案/模块名）
├── config/
│   ├── __init__.py
│   ├── env.py               # .env、模型价格配置解析/加载/保存（原子写入）
│   ├── profiles.py          # API 档案域（api_profiles.json）：读取/归一化/写入/解析/迁移（自 env.py 拆出，审计 G7）
│   ├── disclaimer_state.py  # 免责声明状态管理（DISCLAIMER_VERSION、config/.disclaimer_state.json）
│   ├── onboarding_state.py  # 首启向导状态管理（ONBOARDING_VERSION、config/.onboarding_state.json）
│   ├── version.py           # 版本单一来源 app_version()：VERSION 文件 → frozen BUNDLE_ROOT → 0.0.0-dev
│   └── logging_config.py    # 统一日志配置（按模块拆分 + 文件轮转 + 全量留底 + 线程/逃逸异常钩子）
```

路径常量（`env.py` 顶部，按 `IS_FROZEN` 区分）：

- `PROJECT_ROOT`：可写运行时根。开发态 = 项目根；frozen 下 = exe 所在目录，承载 `config.env` / `logs/` / 用户运行时数据
- `BUNDLE_ROOT`：只读打包资源根。开发态 = 项目根；frozen 下 = `_internal/`，承载静态数据 / 模板 / 图片 / OCR 模型
- `DEFAULT_ENV_FILE` / `DEFAULT_PRICING_FILE` / `DEFAULT_PROFILES_FILE` 分别指向 `PROJECT_ROOT/config.env` / `PROJECT_ROOT/config/model_pricing.json`（6b21d75 由 `BUNDLE_ROOT` 换根：用户可改配置读写均在运行时根，frozen 首启从打包默认部署可改副本、升级不覆盖）/ `PROJECT_ROOT/config/api_profiles.json`（含敏感 Key，故落可写运行时根、不进只读包）
- 共享资源目录（`6c674df` 路径常量收敛后的单一权威源）：`IMAGES_DIR` = `BUNDLE_ROOT/images`（随包头像基线）、`IMAGES_OUTPUT_DIR` = `PROJECT_ROOT/images`（可写，crawler 下载写盘；读取侧两级回退——本目录优先、`IMAGES_DIR` 兜底，见 `ui/shared/portrait.find_portrait_path()`，6b21d75）、`SCREENSHOTS_DIR` = `PROJECT_ROOT/screenshots`（推荐/实战/巅峰赛面板与采集服务共用）
- `OCR_CONFUSION_OVERRIDES_PATH`：`PROJECT_ROOT/data/ocr_confusion_overrides.json`，OCR 混淆白名单路径单一事实源——UI「白名单配置」界面写入、识别侧（`character_similarity`）读取，双侧只引用不再各自拼接
- `is_full_build()`：开发态恒 True；frozen 下读 `BUNDLE_ROOT/.full_build` 标记，精简版（无 RAG 维护页）由此守卫

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
    sys.stdout.reconfigure(encoding="utf-8")    # cmd 默认 GBK，避免中文乱码（windowed 模式 stdout/stderr 可能为 None，需守卫）

QApplication.setHighDpiScaleFactorRoundingPolicy(PassThrough)
app = QApplication(sys.argv)
app.setApplicationName("名将杀 Agent")
app.setOrganizationName("MingJiangSha")
app.setApplicationVersion(app_version())   # 版本单一来源：src/config/version.py 读 VERSION 文件
install_chinese_qt_translator(app)
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MingJiangSha.MJSAgent")   # 任务栏图标修正
install_app_icon(app)
app.setStyleSheet(GLOBAL_STYLE)

if should_show():                  # 免责声明：先于 OCR 预热，未同意不进入加载流程
    if DisclaimerDialog().exec() == QDialog.Accepted:
        accept_disclaimer()        # 写入 config/.disclaimer_state.json
    else:
        sys.exit(0)                # 用户未接受，退出应用

splash = _create_startup_splash()
splash.show()
app.processEvents()
window = MainWindow()
if os.environ.get("MJS_ONBOARDING_FORCE") == "1":      # 发版 smoke 通道：预热前构造向导
    splash.close()                                      # splash 置顶且未 finish，留着会遮挡向导
    window._dialogs.open_onboarding()
window.start_ocr_warmup()
splash.showMessage("正在加载 OCR 模型…")
window.wait_ocr_warmup(timeout_ms=120_000)            # 覆盖 OCR 引擎冷加载（实测可达 90 秒+，占 GIL）
window.show()
splash.finish(window)
from src.config.onboarding_state import should_show_state
if should_show_state():                                # 首启向导：状态文件缺失/版本不符时弹一次
    window._dialogs.open_onboarding()
sys.exit(app.exec())
```

关键设计点：

1. **frozen 首启骨架补齐** — `_ensure_clean_runtime()` 仅 frozen 态执行：从 `_internal/config.env.example` 复制出 `config.env`（用户填 Key），再把 `_internal/config/ocr_rois.default.json` 复制为 `config/ocr_rois.json`（用户 ROI 可编辑副本，缺失才复制），并把 `faction_colors.json` / `model_pricing.json` 两份用户可改配置从 `BUNDLE_ROOT/config/` 复制为 `PROJECT_ROOT/config/` 可改副本（6b21d75，只补缺失——这两类配置的读写已换根到运行时根，不部署会因基线在只读包内而不可改）；创建 `data/` / `logs/` / `config/` / `templates/` / `images/` 目录，将 `_internal/data/` 下静态资源（核心库 json / 官方榜单 csv / RAG 语料 / 评估集 / raw_guides）递归复制到 `PROJECT_ROOT/data/`（只补缺失、不覆盖已有数据），并把元规则母本 `元规则整理-完整版.md` 部署到 `PROJECT_ROOT/docs/`（`build_rule_corpus` 等维护脚本读该路径）。开发态直接返回。
2. **抑制控制台弹窗** — `_install_no_window_patch()` 在 frozen 下给 `subprocess.Popen.__init__` 注入 `CREATE_NO_WINDOW`，避免 adb 等控制台子程序触发 Windows 新建控制台的轮询黑窗。`subprocess.run` 内部走 Popen，一并覆盖。开发态不 patch，保留控制台便于调试。
3. **OCR 预热阻塞式** — `wait_ocr_warmup(timeout_ms=120_000)` 在启动画面上完成 OCR 引擎冷加载。因引擎初始化长时间持有 Python GIL，若与主窗口事件循环并发运行会导致界面卡死，故先预热后 `window.show()`。超时覆盖 120 秒，防止冷加载超过默认值时窗口已显示但预热仍阻塞 UI。
4. **启动页异常兜底** — `MainWindow()` 或预热期间抛异常时，`splash.close()` + `logger.exception` + `QMessageBox.critical`，并 `sys.exit(1)`，避免启动页残留。
5. **frozen 重入走 runpy** — `__main__` 分支中，`IS_FROZEN` 且 `sys.argv[1]=="-m"`（带模块参数）时以 `runpy.run_module()` 模块模式运行（AI 攻略/相性/武将生成走 `src.scraper.ai_batch` 等 `-m` 子脚本），防止 exe 重入又拉起一个 GUI 实例。开发态 python 自带 `-m` 处理，不触发此分支。
6. **首启资料部署标记**（b3327f1）— `data/.deployed` 记录源树签名"文件数:最新mtime秒"（`_bundle_data_signature`），签名一致则跳过约 280MB 的逐文件 rglob 部署扫描；签名变化（升级包带新数据）仍会补齐。范式同 `engine_loader` 的 `.synced`。
7. **启动期轻运维**（c88bf63）— 日志初始化后调用 `_prune_old_screenshots(keep_days=14)` 回收 `screenshots/` 顶层 mtime 超 14 天的 `*.png`（子目录标注样本被 OCR 回归测试引用、不动；失败仅 warning 不阻断启动）；主窗口显示后 `QTimer.singleShot(120_000, coordinator.auto_check_if_due)` 触发每日一次的自动公告检查（忙碌/冷却期内静默跳过，标记先写、当日失败不重试）。
8. **首启向导三段体检**（6962151 / 12c7efb）— 窗口显示后（预热已返回、事件循环尚未接管）以 `should_show_state()` 判定是否弹向导：状态文件缺失、损坏或 `ONBOARDING_VERSION` 不符时弹一次，跳过（`mark_skipped()`）或完成后不再打扰；向导三段（① 运行环境：doctor 子集离线检查 → ② 模拟器连接：ADB 探测/连接/分辨率体检 → ③ 试识别：预热门禁/悬挂复位/试识别），状态持久化 `config/.onboarding_state.json`（`mark_step()` 按段记录完成时间）。`MJS_ONBOARDING_FORCE=1` 提供发版 smoke 通道（预热前构造向导，splash 先 close 防遮挡）；`dialog_coordinator.open_onboarding()` 外层包异常保护（向导自身异常不演变为应用启动失败，记日志+告警后照常收尾）。

### 3.2 配置加载优先级

```
API 档案 (api_profiles.json)  >  config.env  >  环境变量  >  默认值
```

`get_api_config()` 采用三段式解析：

```
┌─ 档案文件存在且有可用档案 ──────→ 返回首个可用档案三件套
├─ 档案文件存在但无可用档案 ──────→ 仅环境变量 + 默认值兜底（刻意不读 config.env 旧键，让"停用"真正生效）
└─ 档案文件不存在（从未配置档案） ─→ 走 _legacy_api_config：config.env → 环境变量 → 默认值
```

"可用"按 `_usable_profile_config()` 判定（`enabled` + `api_url` 非空 + 供应商 Key 语义），与"启用"是两件事：唯一启用但 URL 为空或 Key 缺失的档案会被视为不可用而跳过。

启用互斥语义：`_normalize_profiles()` 在加载/保存时保证同时至多一个 `enabled=true`（启用即当前使用的 API）。多个 enabled 时保留第一个、其余置 false 并记 warning，数据层面即生效，避免历史文件多 enabled 导致界面显示多个启用。空 URL 不回退 DeepSeek 默认（防止跨供应商发错端点），视为无效跳过；空 Key 仅对 `requires_key=False` 的供应商（如 ollama）允许。`is_default` 字段已废弃，旧文件中的 `is_default` 被静默丢弃。

档案可用性的唯一判定是 `_usable_profile_config(profile)`：`enabled` 为真、`api_url` 非空、且（供应商 `requires_key=True` 时）`api_key` 非空，三者同时满足才返回三件套（`model` 为空时回退 `DEFAULT_MODEL`），否则返回 `None`。`get_api_config()`、`resolve_api_config(name)` 与 `has_available_api_profile()` 三条路径共用该判定，避免配置对话框的可用性判断与生成链路漂移。

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

`load_env_config(env_path)` 解析 `.env` 后按键元表 `_ENV_KEY_SPECS`（env.py 内唯一事实源，P1-4/P1-7 收口；1bfd65b 定稿为 `_EnvKeySpec` NamedTuple 五字段 `cfg_key` / `type` / `default` / `getter` / `default_from`，现 38 键：runtime 6 + mumu 21 + 透传 11）完成大写 KEY → 内部小写 key 映射与类型转换（int/bool/float/str 四型，str 不经转型透传；int/float 转换失败告警并丢弃该键，由 getter 补默认值）；两个 getter 缩为一行委托 `_config_with_defaults(config, getter)` 按域补默认，键集与默认值同源于该表，新键只登记元表一处。`default_from` 表达级联默认：`mumu_hero_selection_threshold` 未配置时回退 `mumu_ocr_match_threshold` 实配值（消除 getter 特判），多品牌连接三键（`EMULATOR_ADB_PATH` / `EMULATOR_ADB_PORT` / `EMULATOR_SCREENSHOT_MODE`）未配置时回退旧 `MUMU_ADB_*` 实配值（老 config.env 不改字平滑升级，持久化始终写新键）。此前 key_mapping / 三张类型清单 / `get_runtime_params` 与 `get_mumu_config` 默认值四处分头登记，063dc47（截图模式键漏出 getter 成死开关）即漏登记事故，单源后由 `tests/test_env_config.py` 键全集断言守护（`_SENTINELS` 全量键哨兵表 + 8 函数：bool 七变体 / 级联默认 / 单源 `is` 断言 / 分域划分）。

`get_runtime_params()` 返回：`requests_per_minute`(30) / `max_retries`(3) / `max_output_tokens`(32768，cf9c927 由 16384 上调；P1-7 起单源于 `env.DEFAULT_MAX_OUTPUT_TOKENS`，`api_generator.MAX_OUTPUT_TOKENS` 仅引用) / `http_timeout`(300) / `log_level`("INFO") / `log_to_file`(True)。

`get_mumu_config()` 返回模拟器配置：`mumu_adb_path`("") / `mumu_adb_port`(0) / `mumu_screenshot_mode`("auto"，MUMU_SCREENSHOT_MODE，透传给 capture_service；063dc47 补回返回字典——此前该键漏出、文档承诺的排障手段实为死开关) / `mumu_ocr_enabled`(False) / `mumu_ocr_poll_mode`(False) / `mumu_ocr_auto_switch_tab`(False) / `mumu_ocr_poll_interval`(2) / `mumu_ocr_match_threshold`(0.8) / `mumu_hero_selection_threshold`(回退 match_threshold) / `mumu_hero_selection_cooldown`(180) / `mumu_match_guide_threshold`(0.8) / `mumu_ocr_primary_engine`("v6"，B1 新增，v4 为回滚档且主复核引擎互换) / `mumu_ocr_cpu_threads`(6，钉入 onnxruntime intra_op) / `mumu_ocr_recheck_enabled`(False，复核引擎开关) / `mumu_ocr_poll_idle_pause`(True，轮询闲置自动暂停开关，bca4092 新增)。`MUMU_OCR_USE_GPU` 已随 paddle 退役转为死键（doctor `DEAD_CONFIG_KEYS` 登记）。OCR 推理配置（主引擎套件、CPU 线程数）由 `load_env_config()` 完成类型转型后提供给 `engine_loader.create_rapidocr_ocr()`。2026-09 起（cd35c98）另新增模板匹配分层加速与 ADB raw 帧截图提速相关配置参数，详见 `config.env.example`。

闲置暂停阈值分钟数不走配置键：早先的 `MUMU_OCR_POLL_IDLE_MINUTES` 已移除，现为 `PollCoordinator.IDLE_PAUSE_MINUTES = 5` 类常量（`src/ui/app/poll_coordinator.py`），配置面只保留 `MUMU_OCR_POLL_IDLE_PAUSE` 开关。

元表其余 11 键为透传键（无 getter，调用方直接从 `load_env_config()` 取值，不进 UI 配置对话框）：`api_key` / `api_url` / `model`（旧 `DEEPSEEK_*` 三件套，profiles 域旧链兜底消费）、`mjs_data_repo`（pull_data 私有仓位置）、`recommendation_p_floor` / `recommendation_ban_weight` / `recommendation_sigmoid_k` / `recommendation_low_win_rate_gap`（推荐指数计算参数）、`data_freshness_timeline_stale_days`（ae92c1d 随数据新鲜度芯片新增：公告时间轴距今天数超过该值视为不健康，武将新鲜度退化为纯年龄估算，非正整数回退默认 14；`config.env.example` 文件尾新增「数据新鲜度（状态栏芯片）」段）、`data_freshness_stale_green_days` / `data_freshness_stale_red_days`（数据新鲜度档位阈值：[0,绿) 绿 / [绿,红) 黄 / ≥红 红，默认 7/14；官方榜单约每半月更新，写死 14 天会让榜单长期停在黄/红并盖掉武将信号（TD-A6-001），由 `business/freshness.py::_stale_thresholds()` 消费）。

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

root 级别下限 WARNING（`root.setLevel(max(level, logging.WARNING))`，即使用户把 `LOG_LEVEL` 调到 DEBUG，root 仍不低于 WARNING）：第三方库（chromadb / transformers 等）作为 root 的直接子且级别 NOTSET，继承 root 后其 INFO/DEBUG 在 logger 层即被挡、不创建 LogRecord，实现零库名清单的高效压制。项目 `src` / `subprocess` 前缀单独设 `DEBUG` 全量创建，供 `debug.log` 留底与子进程输出转发。

**控制台输出队列化（79fc4a0，防卡死）**：root 的控制台 handler 不再直接 `StreamHandler(sys.stdout)`，而是包一层 `_QueuedConsoleHandler`（继承 `logging.handlers.QueueHandler`，无界 `SimpleQueue` + 后台 `QueueListener` 消费线程）——调用线程只做入队，控制台写全部移出调用线程。背景：Windows 经典控制台的**快速编辑选中**会无限期阻塞 `WriteConsoleW`（2026-10-24 事故：GUI 主线程在菜单槽的 `logger.info` 上悬死，按 Esc 才解冻）；队列化后控制台再被卡住最多积压/延迟控制台日志，不再冻结 UI。文件 handler 不经队列，仍同步落盘不受影响。`close()` 顺带停消费线程并关掉底层控制台 handler，避免线程与 handler 随 `setup_logging` 重入轮次累积。

**线程与逃逸异常钩子**：`setup_logging()` 同时注册两类兜底钩子——`threading.excepthook`（R2，`_thread_excepthook`）把后台线程未捕获异常记为带堆栈 ERROR（`SystemExit` 视为正常退出不记录）；链式 `sys.excepthook`（80cc75b，`_install_sys_excepthook`）——PySide6 6.11 实测 `QThread.run()` 异常走 `sys.excepthook` 而非 `threading.excepthook`，新钩子把逃逸异常记为带堆栈 ERROR（logger 名 `excepthook`）后回调前一 hook 保留控制台行为，`KeyboardInterrupt` 直接透传，`_MANAGED_EXCEPTHOOK_ATTR` 标记保证幂等不重复包裹。

**子进程日志策略**：由桌面应用启动的 QProcess 子进程设置 `MJS_QPROCESS_CHILD=1` 后跳过所有文件 Handler，仅输出控制台，stdout/stderr 由父进程统一收集并路由到对应日志文件，避免多进程同时轮转同一组文件导致 Windows 文件占用与备份竞争。`src.scraper.ai` / `subprocess.ai` 命名空间对应的 `ai_generation.log` handler 采用 `keep_debug=True` + 独立路由设计，使子进程原始输出（含 429 / length / JSON 等失败原因）在 root WARNING 下也不丢失。

### 3.5 模型价格

- `config/model_pricing.json`（frozen 下读写均在运行时根 `PROJECT_ROOT/config/`，6b21d75 由只读 `BUNDLE_ROOT/config/` 换根，首启由 `_ensure_clean_runtime` 部署可改副本）是版本控制的模型价格来源。未知模型不会套用默认价格，而是返回"无法自动估算"（`get_model_pricing` 返回 `None`）。
- 价格文件包含 `currency`、`unit`、`updated_at` 和 `models`；计价单位为"百万tokens"，每个模型维护 `input_per_million`、`output_per_million`、可选 `cached_input_per_million` 单价。2026-09 新增 `sensenova-6.8-flash-lite`（74234a8），当前表内实测 3 个模型（deepseek-v4-pro / deepseek-v4-flash / sensenova-6.8-flash-lite）。
- `load_pricing_config(path)` 负责读取价格表，文件不存在或格式无效时返回默认空表 `{"currency":"CNY","unit":"百万tokens","updated_at":"","models":{}}`。
- `save_pricing_config(path, data)` 负责 UTF-8 无 BOM、LF 换行的原子写入（9737d52 起委托 `json_repository.atomic_write_text`，函数内延迟导入避免 config↔data 循环初始化）。
- `get_model_pricing(model)` 校验单价必须为非负数字（且不是 bool），非法返回 `None`。
- API 配置对话框的"价格配置"页签直接维护该文件，保存前会校验模型名称唯一且单价为非负数。
- pricing 域回退语义由 `tests/test_env_config.py` 守护（b5e8e4f 补齐，共七函数十五用例）：文件缺失 / JSON 损坏 / 根非对象回退空表；负价格 / 非数字 / 布尔误判为价格返回 `None`；未知模型返回 `None`。

### 3.6 势力配色配置

`config/faction_colors.json` 是势力配色与筛选展示顺序的唯一配置源，由 `src/ui/shared/faction_colors.py` 读取：

```json
[
  {"faction": "西周", "color": "#D95471"},
  {"faction": "韩", "color": "#347632"},
  {"faction": "赵", "color": "#9D7732"},
  {"faction": "魏", "color": "#B02D34"},
  {"faction": "楚", "color": "#AE2D34"},
  {"faction": "燕", "color": "#2B2D59"},
  {"faction": "齐", "color": "#54196A"},
  {"faction": "秦", "color": "#3A3737"},
  {"faction": "张楚", "color": "#C5C5C4"},
  {"faction": "西楚", "color": "#CACAC8"},
  {"faction": "西汉", "color": "#8F231F"},
  {"faction": "东汉", "color": "#AC2C32"},
  {"faction": "黄巾", "color": "#AB7E3E"},
  {"faction": "曹魏", "color": "#2F5470"},
  {"faction": "蜀汉", "color": "#921822"},
  {"faction": "孙吴", "color": "#297540"},
  {"faction": "西晋", "color": "#3A5E89"},
  {"faction": "东晋", "color": "#566D81"}
]
```

**结构升级说明**（2026-09-15）：原 dict 结构 `{"秦": "#8B4513", "汉": "#B22222", ...}` 升级为数组结构 `[{faction, color}, ...]`。数组位置即筛选界面的势力展示顺序——配置方按所需展示次序排列条目，无需额外排序字段。`load_faction_colors()` 返回 dict（字典插入顺序即配置顺序），`get_faction_colors()` 带缓存，`reload_faction_colors()` 清缓存重读。`sort_factions_by_config(factions)` 按配置顺序排序势力名列表，配置外的势力按码点序追加尾部。

配置文件的唯一权威源是版本控制的 `config/faction_colors.json`。读写路径 `FACTION_COLORS_FILE` 位于 `src/ui/shared/faction_colors.py`，指向运行时可写根 `PROJECT_ROOT/config/`（6b21d75 由 `BUNDLE_ROOT` 换根：frozen 下首启由 `_ensure_clean_runtime` 从打包默认部署可改副本，用户自定义不随升级包丢失；`faction_color_dialog.py` 已删除本地硬编码路径、改从 `ui.shared.faction_colors` 导入）。UI 的势力筛选与配色面板（`faction_colors_dialog`）可直接编辑此文件，保存后调用 `reload_faction_colors()` 即时生效。

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
    # 2. 追加新 key；3. 原地更新既有 key；4. 组装结果交 atomic_write_text 原子覆盖
    atomic_write_text(env_path, "\n".join(result_lines) + "\n")
```

> **设计思路：** 直接写原文件如果中途崩溃会导致 .env 半损坏。写盘统一委托 `json_repository.atomic_write_text`（mkstemp 唯一临时名 + fsync + `Path.replace` 原子替换），不再自建固定名 `.tmp`——固定名在并发写同一路径时会互相截断。保留注释与无关键避免用户手写配置被覆盖丢失；若旧文件读失败则直接 `raise`，而非用空内容静默覆盖。

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
    # 兜底钩子：后台线程与 QThread 逃逸异常记带堆栈 ERROR（幂等，不重复包裹）
    threading.excepthook = _make_thread_excepthook()
    _install_sys_excepthook()
```

> **设计思路：** 只清理项目自身创建的 Handler，避免重复注册，同时不破坏外部日志 Handler。root 下限 WARNING + `src`/`subprocess` 单独设 DEBUG 形成"倒置"——第三方库静默、项目全量留底，`debug.log` 用 2 倍轮转上限承载。钩子链式回调前一 hook，KeyboardInterrupt 透传。

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

### 4.4 首启向导状态容错（缺失/损坏/版本不符一律重新弹出）

```python
def should_show_state(state_file: Path = _STATE_FILE) -> bool:
    """首启向导是否需要展示：从未完成，或记录的引导版本已过期。"""
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        recorded = str(data["onboarding_version"])
    except FileNotFoundError:
        return True
    except (OSError, ValueError, KeyError, TypeError) as e:
        logger.warning("首启向导状态文件读取失败，按未完成处理: %s", e)
        return True
    return recorded != ONBOARDING_VERSION
```

> **设计思路：** 与 `disclaimer_state` 完全同范式——文件缺失、JSON 损坏、字段非法一律按"未完成"处理（记日志后重新弹出），而非静默跳过。`onboarding_version` 升版意味着引导内容实质变化，所有老用户（含已跳过的）都会重新看到向导，这是有意的产品决策。`_read()` 辅助函数在不可用时返回全新状态（不覆盖，由下一次写入重建），避免状态文件被意外清空。

---

## 五、接口说明

本模块不提供外部 API 接口，但提供以下公共函数/常量。多数经 `src.config.__init__.py` 统一导出；`IS_FROZEN` / `PROJECT_ROOT` / `BUNDLE_ROOT` / `IMAGES_DIR` / `IMAGES_OUTPUT_DIR` / `SCREENSHOTS_DIR` / `OCR_CONFUSION_OVERRIDES_PATH` / `is_full_build()` / `get_mumu_config()` / `has_available_api_profile()` 不经 `__init__.py`，调用方直接从 `src.config.env` 导入；`disclaimer_state` / `onboarding_state` 各函数的调用方亦直接从对应子模块导入（不经 `__init__.py`）：

| 函数 / 常量 | 文件 | 返回 | 说明 |
|-------------|------|------|------|
| `IS_FROZEN` | `env.py` | `bool` | 是否 PyInstaller 打包态 |
| `PROJECT_ROOT` / `BUNDLE_ROOT` | `env.py` | `Path` | 可写运行时根 / 只读打包资源根 |
| `DEFAULT_ENV_FILE` / `DEFAULT_PRICING_FILE` / `DEFAULT_PROFILES_FILE` | `env.py` | `Path` | 各配置文件默认路径 |
| `IMAGES_DIR` / `IMAGES_OUTPUT_DIR` / `SCREENSHOTS_DIR` | `env.py` | `Path` | 共享资源目录：只读头像 / 可写头像下载 / 截图（推荐·实战·巅峰赛·采集共用） |
| `OCR_CONFUSION_OVERRIDES_PATH` | `env.py` | `Path` | OCR 混淆白名单路径单一事实源（`data/ocr_confusion_overrides.json`）：白名单配置界面写入、`character_similarity` 识别侧读取 |
| `DEFAULT_API_URL` / `DEFAULT_MODEL` | `env.py` | `str` | DeepSeek 默认 API 端点与模型 |
| `PROVIDER_PRESETS` / `PROVIDER_LABELS` | `env.py` | `dict` | 供应商预设（URL/默认模型/是否需要 Key）与展示名 |
| `is_full_build()` | `env.py` | `bool` | frozen 下 `.full_build` 标记是否存在 |
| `parse_env_file(path)` | `env.py` | `dict[str, str]` | 解析 .env 文件（含注释、引号处理） |
| `load_env_config(path)` | `env.py` | `dict` | 解析并完成类型转型（整数/布尔/浮点） |
| `get_api_config()` | `profiles.py` | `dict` | 三段式获取 API 配置：首个可用档案 → 仅环境变量+默认值（档案文件存在但无可用档案）→ 旧链（`config.env` → 环境变量 → 默认值） |
| `get_runtime_params()` | `env.py` | `dict` | 运行参数：RPM / 重试 / max_output_tokens / 超时 / 日志等级 / log_to_file |
| `get_mumu_config()` | `env.py` | `dict` | 模拟器配置（ADB / OCR / 阈值 / 冷却） |
| `save_env_file(path, data)` | `env.py` | `None` | 原子写入 .env（保留注释与无关键） |
| `load_pricing_config(path)` | `env.py` | `dict` | 加载模型价格配置 |
| `save_pricing_config(path, data)` | `env.py` | `None` | 原子保存模型价格配置 |
| `get_model_pricing(model)` | `env.py` | `dict \| None` | 查询模型单价，未知模型返回 `None` |
| `load_api_profiles(path)` | `profiles.py` | `dict` | 读取 API 档案（含归一化与启用互斥） |
| `save_api_profiles(data, path)` | `profiles.py` | `None` | 原子保存 API 档案（空档案自动删文件回到旧链兜底） |
| `list_api_profiles()` | `profiles.py` | `list[dict]` | 列表视图模型：api_key 以 `has_key` 布尔代替，不回显明文（当前 SettingsDialog 直接消费 `load_api_profiles()`，本函数主要供回归测试锚定掩码语义） |
| `get_api_profile(name)` | `profiles.py` | `dict \| None` | 按名称取完整档案（含 api_key），仅供任务解析，不入日志/UI |
| `resolve_api_config(name)` | `profiles.py` | `dict` | 任务侧唯一 API 解析入口：指定档案 → 默认解析 |
| `has_available_api_profile()` | `profiles.py` | `bool` | 是否存在可用（enabled+URL 非空+供应商 Key 语义）的档案 |
| `migrate_legacy_api_config(env_path, profiles_path)` | `profiles.py` | `bool` | 旧 DEEPSEEK_* 三件套 → deepseek-main 档案（幂等） |
| `setup_logging(...)` | `logging_config.py` | `None` | 初始化日志系统（幂等，只清理自身 Handler；同时注册 threading.excepthook 与链式 sys.excepthook） |
| `app_version()` | `version.py` | `str` | 版本单一来源（lru_cache）：依次读 `PROJECT_ROOT/VERSION` → frozen `BUNDLE_ROOT/VERSION`，均失败回退 `"0.0.0-dev"`；消费方为 main.py 启动日志与 `setApplicationVersion`、关于对话框（80cc75b 起，原硬编码 v0.1.0）与 release.py 发版校验 |
| `DISCLAIMER_VERSION` | `disclaimer_state.py` | `str` | 免责声明版本常量（当前 `"1.3"`），仅版本变化时要求重新确认；TERMS/LICENSE 实质修订时须与常量同批递增（735a2d2） |
| `should_show(state_file)` | `disclaimer_state.py` | `bool` | 免责声明是否需要展示：从未接受或接受版本 ≠ `DISCLAIMER_VERSION` 时为 True；状态文件缺失/损坏/字段非法一律按"未接受"处理（记日志后重新弹窗） |
| `accept(state_file, version)` | `disclaimer_state.py` | `None` | 记录用户接受的文本版本与时间（`config/.disclaimer_state.json`）；`main.py` 在 `should_show()` 为真时弹 `src/ui/app/disclaimer_dialog.py`，用户同意后调用，拒绝则退出应用 |
| `ONBOARDING_VERSION` | `onboarding_state.py` | `str` | 首启向导版本常量（当前 `"1"`），引导内容实质变化时递增驱动重新展示；状态文件缺失/损坏/版本不符时重新弹出 |
| `STEP_KEYS` | `onboarding_state.py` | `tuple[str, ...]` | 引导步骤合法键 `("env", "device", "recognize")`：段 1 环境自检 / 段 2 模拟器连接 / 段 3 试识别 |
| `should_show_state(state_file)` | `onboarding_state.py` | `bool` | 首启向导是否需要展示：从未完成或记录的引导版本已过期；状态文件缺失/损坏/字段非法一律按"未完成"处理 |
| `mark_step(step, state_file)` | `onboarding_state.py` | `None` | 记录单个引导步骤完成时间（step ∈ STEP_KEYS）；未知步骤抛 `ValueError` |
| `mark_skipped(state_file)` | `onboarding_state.py` | `None` | 记录跳过引导，此后不再自动弹出（直至版本升版或 `reset()`） |
| `reset(state_file)` | `onboarding_state.py` | `None` | 清除状态文件，供「帮助 → 重看首启引导」与自测复现使用 |
| `install_chinese_qt_translator(app)` | `ui/app/chinese_translator.py` | `ChineseQtTranslator` | 安装应用级 Qt 标准控件中文翻译器 |
| `install_details_button_translator(msgbox)` | `ui/app/chinese_translator.py` | `_DetailsButtonFilter` | 为 QMessageBox 安装详情按钮翻译过滤器（"查看详情/隐藏详情"），随对话框销毁；Qt 内部展开/收起时直接 setText 不走 QTranslator，需在 layout 变化时遍历子按钮翻译 |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 被依赖 | — | 配置的唯一权威来源：多数模块 `from src.config.env import ...` 直接导入，API 档案域自 `src.config.profiles` 导入（自 env.py 拆出，审计 G7），部分经 `src.config.__init__` 聚合导出 |
| 被依赖 | `src.ui.configuration.settings_dialog` | 档案列表/编辑面板与价格页签：`load_api_profiles()` / `save_api_profiles()` / `load_pricing_config()` / `save_pricing_config()` / `save_env_file()` / `PROVIDER_PRESETS` / `PROVIDER_LABELS` / `parse_env_file()` |
| 被依赖 | `src.ui.generation.backend_choose_dialog` | `has_available_api_profile()` 判定 API 后端是否可用（与生成链路共用同一可用性判定） |
| 被依赖 | `src.scraper.ai.batch` | `resolve_api_config(None)` 取生效档案；`get_runtime_params()` + `setup_logging()` 初始化 CLI 运行参数 |
| 被依赖 | `src.scraper.official_source.full` / `incremental` | `get_runtime_params()` + `setup_logging()` 初始化官方源采集 CLI |
| 被依赖 | `src.rag.indexer` | 索引构建 CLI 入口调用 `setup_logging()` |
| 被依赖 | `src.business.ai_cost` / `src.scraper.ai.prompt_utils` | `get_api_config()["model"]` 取当前生效模型做成本估算；`get_model_pricing()` 查单价 |
| 被依赖 | `src.business.rag.refinement_service` | `resolve_api_config(profile_name)` + `PROVIDER_PRESETS` 解析指定档案 |
| 被依赖 | `src.ui.app.main_window` / `src.ui.app.app_services` | `get_mumu_config()` 注入 `CaptureService` / `OcrService.update_config()`；`_open_mumu_config()` 经 `mumu_config_coordinator.persist_mumu_env_config()`（内部 `save_env_file()`）回写；`is_full_build()` 守卫 RAG 维护页 |
| 被依赖 | `src.ocr.character_similarity` / `src.ui.configuration.whitelist_config_dialog` | 共享 `OCR_CONFUSION_OVERRIDES_PATH` 作为 OCR 混淆白名单路径单一事实源（识别侧读取、白名单界面写入） |
| 被依赖 | `src.ocr.engine_loader` | `primary_suite()` 读 `mumu_ocr_primary_engine`（默认 v6）选主引擎套件；`_cpu_threads()` 读 `mumu_ocr_cpu_threads` 钉 onnxruntime intra_op 线程 |
| 被依赖 | `src.rag.config` | RAG 语料/向量索引与预算配置（RAG_ENABLED / RAG_TOP_K / RAG_PROMPT_CHARS / RAG_BROWSER_PROMPT_CHARS / RAG_SYNERGY_PROMPT_CHARS / RAG_MODEL_DIR），由 AI 批量生成模块使用 |
| 被依赖 | 所有模块 | 共享同一日志系统；QProcess 子进程经 `MJS_QPROCESS_CHILD=1` 环境变量跳过文件 Handler、仅输出控制台，stdout/stderr 由父进程转发落盘 |
| 依赖 | `src.ui.app.main_window` | 应用入口创建 MainWindow 并调用 `start_ocr_warmup()` / `wait_ocr_warmup(timeout_ms=120_000)` |
| 依赖 | `src.config.disclaimer_state` | `main.py` 启动时以 `should_show()` 判定是否弹免责声明、`accept()` 落盘确认状态（仅文本版本变化时要求重新确认） |
| 依赖 | `src.config.onboarding_state` | `main.py` 窗口显示后以 `should_show_state()` 判定是否弹首启向导；`onboarding_dialog` 各段经 `mark_step()` 记录完成、`mark_skipped()` 记录跳过（6962151） |
| 依赖 | `src.ui.app.onboarding_dialog` / `src.ui.app.onboarding_fixes` | 首启向导三段体检（环境自检→模拟器连接→试识别）与 doctor 修复文案映射；`dialog_coordinator.open_onboarding()` 外层包异常保护（12c7efb） |
| 依赖 | `src.ui.app.app_icon` / `src.ui.app.chinese_translator` / `src.ui.shared.style` | 启动阶段安装应用图标、Qt 标准控件中文翻译器与全局样式表 `GLOBAL_STYLE` |
| 依赖（传递） | `src.business.emulator.capture_service` → `src.business.recognition.ocr_worker` → `src.ocr.engine_loader` | 启动页阶段完成 OCR 引擎冷加载（RapidOCR/ONNX 双套件），主入口仅触发、不直接引用 |
| 依赖 | `runpy`（frozen 重入） | `-m` 子脚本以模块模式运行，避免 exe 重入拉起 GUI |
---

## 七、本轮文档校准（2026-10-06）

自基线 `885ea96`（2026-10-02 校准）以来的变更：

**新增**
- `src/config/version.py`（31f59be / 80cc75b）：版本单一来源 `app_version()`（lru_cache，VERSION 文件优先 → frozen BUNDLE_ROOT → 回退 `0.0.0-dev`）；VERSION 文件当前 `1.2.1`。消费方：main.py 启动日志（版本/Python/Qt/运行时根/frozen）与 `setApplicationVersion`、关于对话框（原硬编码 v0.1.0）、release.py 发版校验与发版清单
- `logging_config.py` 双钩子：`threading.excepthook`（R2）+ 链式 `sys.excepthook`（80cc75b，QThread 逃逸异常实测走 sys.excepthook；KeyboardInterrupt 透传；幂等标记）
- main.py 启动期轻运维（c88bf63）：`_prune_old_screenshots(keep_days=14)` 截图回收、启动 2 分钟后 `auto_check_if_due()` 每日一次自动公告检查（标记先写、忙碌/冷却静默跳过）
- 首启资料部署标记 `data/.deployed`（b3327f1）：签名一致跳过约 280MB 逐文件扫描

**修正**
- `get_mumu_config()` 补回 `mumu_screenshot_mode`（063dc47）——此前返回字典漏掉该键，`MUMU_SCREENSHOT_MODE` 排障手段实为死开关
- `save_pricing_config` / `save_env_file` 写盘收敛到 `json_repository.atomic_write_text`（9737d52/063dc47，mkstemp 唯一临时名根治固定名 `.tmp` 并发互截；函数内延迟导入避免 config↔data 循环初始化）
- `get_runtime_params` 的 `max_output_tokens` 默认 16384 → 32768（cf9c927，与 `AIBatchGenerator.MAX_OUTPUT_TOKENS` 对齐）
- 配置键面：新增 `MUMU_OCR_PRIMARY_ENGINE`（默认 "v6"）、`MJS_DATA_REPO`（私有数据仓位置）；`MUMU_OCR_USE_GPU` 随 paddle 退役转死键（doctor `DEAD_CONFIG_KEYS` 登记）
- `DISCLAIMER_VERSION` "1.0" → `"1.1"`（735a2d2，TERMS/LICENSE 实质修订须与常量同批递增）
- OCR 推理配置消费方 `paddle_loader.create_paddle_ocr()` → `engine_loader.create_rapidocr_ocr()`（paddle 全栈退役）

---

## 八、本轮文档校准（2026-10-11）

自基线 `0ffed36`（2026-10-06 校准）以来的变更：

**重构收口（行为零变化）**
- **env.py 键元表单源化**（1bfd65b）：`_EnvKeySpec` NamedTuple 五字段（`cfg_key` / `type` / `default` / `getter` / `default_from`）+ `_ENV_KEY_SPECS` 注册表（现 30 键：runtime 6 + mumu 15 + 透传 9）唯一事实源，替代四处手工同步；`load_env_config()` 按 `spec.type` 统一转换（int/float 失败告警并丢弃该键）；新增 `_config_with_defaults(config, getter)` 按域补默认，`default_from` 表达级联默认（`mumu_hero_selection_threshold` 未配置回退 `mumu_ocr_match_threshold` 实配值，消除 getter 特判）；`get_runtime_params` / `get_mumu_config` 缩为一行委托。`DEFAULT_MAX_OUTPUT_TOKENS = 32_768` 单源（`api_generator.MAX_OUTPUT_TOKENS` 引用同对象）。测试补 `_SENTINELS` 全量键哨兵表 + 8 函数（bool 七变体 / 级联 / 单源 `is` 断言 / 分域划分），详见 3.3 节
- **main.py 注释措辞清理**（683fca6）：OCR 预热注释“Paddle 初始化…”→“OCR 引擎初始化…”（纯文档，无行为变化）

**新增配置键**
- `DATA_FRESHNESS_TIMELINE_STALE_DAYS`（ae92c1d）：元表追加透传键（int、default=None、不进 getter 域与 UI 配置对话框）；`config.env.example` 文件尾新增「数据新鲜度（状态栏芯片）」段。语义：公告时间轴距今天数超过该值视为不健康，武将新鲜度退化为纯年龄估算，默认 14（详见 3.3 节）

**打包态读写根与首启部署**（6b21d75）
- frozen 下 `BUNDLE_ROOT` = `<exe>/_internal` 只读、`PROJECT_ROOT` = exe 同级可写，开发态两根相等（口径不变，本轮起全面贯彻）；`DEFAULT_PRICING_FILE` 由 `BUNDLE_ROOT` 换 `PROJECT_ROOT`，与 `faction_colors.json` 一致
- `src/main.py` `_ensure_clean_runtime()` 新增：首启若 `PROJECT_ROOT/config/faction_colors.json` / `model_pricing.json` 缺失，从 `BUNDLE_ROOT/config/` 复制可改副本（ocr_rois 同款范式，只补缺失），详见 3.1 关键设计点 1

**测试**
- `tests/test_env_config.py` 补 pricing 域（b5e8e4f，七函数十五用例）：文件缺失 / JSON 损坏 / 根非对象回退空表；负价格 / 非数字 / 布尔误判返回 `None`；未知模型 `None`（详见 3.5 节）

---

## 九、本轮文档校准（2026-10-23）

自基线 `2081b5b`（2026-10-11 校准）以来的变更：

**新增：首启向导三段体检**（6962151 / 12c7efb）
- `src/config/onboarding_state.py`（96 行，新增文件）：首启向导状态管理，持久化 `config/.onboarding_state.json`，范式同 `disclaimer_state`（文件缺失/损坏/版本不符一律按"未完成"重新弹出）。核心常量 `ONBOARDING_VERSION = "1"`、`STEP_KEYS = ("env", "device", "recognize")`（段 1 环境自检 / 段 2 模拟器连接 / 段 3 试识别）；核心函数 `should_show_state()`（判定是否需要展示）、`mark_step(step)`（记录步骤完成时间）、`mark_skipped()`（记录跳过）、`reset()`（清除状态文件供重看）。详见 3.1 关键设计点 8 与五节接口表
- `src/main.py` 接入（+12 行）：窗口显示后以 `should_show_state()` 判定弹首启向导；`MJS_ONBOARDING_FORCE=1` 提供发版 smoke 通道（预热前构造向导，splash 先 close 防遮挡）
- `src/ui/app/dialog_coordinator.py::open_onboarding()` 外层包异常保护（12c7efb）：向导自身异常不演变为应用启动失败，记日志+告警后照常收尾

**修正与配置**
- `DISCLAIMER_VERSION` `"1.1"` → `"1.3"`（disclaimer_state.py），TERMS/LICENSE 附加条款实质修订；TERMS.md 节号同步由「第 9 节」→「第 10 节」
- `env.py` 新增 2 个透传键（+4 行）：`DATA_FRESHNESS_STALE_GREEN_DAYS` / `DATA_FRESHNESS_STALE_RED_DAYS`（int、default=None，不进 getter 域与 UI 配置对话框）。语义：数据新鲜度档位阈值 [0,绿) 绿 / [绿,红) 黄 / ≥红 红，默认 7/14；官方榜单约每半月更新，写死 14 天会让榜单长期停在黄/红并盖掉武将信号（TD-A6-001），由 `business/freshness.py::_stale_thresholds()` 消费。键元表现 32 键（详见 3.3 节）

---

## 十、本轮文档校准（2026-10-25）

自基线 `a63bc3c`（2026-10-23 校准）以来的变更（覆盖 8f2e70a / ed8b4ac / 79fc4a0 中涉及 config 域的部分）：

- **env.py 多品牌连接键**（8f2e70a / ed8b4ac，+15 行）：新增 6 键——`EMULATOR_TYPE`（品牌/连接方式标识，取值域 `src.capture.probers.EMULATOR_TYPES`）、`EMULATOR_BRAND`（切 PC 客户端期间记住上次模拟器品牌）、`PC_WINDOW_TITLE`（PC 直连目标窗口标题，默认"名将杀"）、`EMULATOR_ADB_PATH` / `EMULATOR_ADB_PORT` / `EMULATOR_SCREENSHOT_MODE`（三键带 `default_from="mumu_*"` 级联回退，老 config.env 不改字平滑升级；持久化始终写 `EMULATOR_*`，写入后新键优先生效、旧 `MUMU_ADB_*` 残行为惰性数据）。键元表 32 → **38 键**（runtime 6 + mumu 21 + 透传 11），消费方为 `CaptureConnectionManager._conn_key()`（同语义兼容读取）与 `AdbCapture(emulator_type=)`。详见 §3.3
- **logging_config.py 控制台队列化**（79fc4a0，+32 行）：新增 `_QueuedConsoleHandler`（QueueHandler + QueueListener 后台消费线程），控制台写移出调用线程——Windows 经典控制台快速编辑选中会无限期阻塞 `WriteConsoleW`（2026-10-24 事故：GUI 主线程在 `logger.info` 上悬死，Esc 才解冻）；文件 handler 不经队列仍同步落盘。详见 §3.4
- **doctor.py 设备检查适配**（feee42b / ed8b4ac）：ADB 路径读取改 `emulator_adb_path`（旧键回退）；`adb devices` 在线序列号匹配放宽为 `127.0.0.1:<port>` 或 `emulator-<n>`（雷电等品牌可能注册为 adb 主动扫描区命名）；无在线设备时修复文案按形态分立（蓝叠提示先在设置中开启 ADB）
- **environment.yml / environment-ci.yml**（ed8b4ac）：新增 `windows-capture==2.0.1`（PC 客户端 WGC 采集主路径，Win10 1903+，失败自动退 BitBlt）

**本轮其余 config 域文件**（version.py / onboarding_state.py / disclaimer_state.py 等）无代码变更。
