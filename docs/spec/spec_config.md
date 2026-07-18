# 配置管理规范

> 长期设计规则与决策依据，覆盖 .env 加载、类型转型与日志配置。

## 一、.env 文件设计

### 规则 1.1：config.env 是唯一持久配置存储

所有用户可修改的配置项（API key、模型名、ADB 路径等）存储在项目根目录的 `config.env` 中。不使用 `settings.json` 或数据库。

**为什么：** 配置文件是纯文本，用户可手动编辑、git diff 可见、生成环境无需额外数据库。`KEY=VALUE` 格式跨平台兼容，不引入 YAML/TOML 解析依赖。

### 规则 1.2：优先级链：config.env > 环境变量 > 默认值

```python
api_key = config.get("api_key") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
```

**为什么：** config.env 是用户显式配置理应最高优先级。os.getenv 作为 fallback 允许 CI/临时运行时通过环境变量注入配置，无需写文件。默认值兜底保证程序至少能启动提示用户配置。

## 二、key_mapping 机制

### 规则 2.1：双阶段加载

`parse_env_file()` 返回 `dict[str, str]`（所有值都是字符串），`load_env_config()` 通过 `key_mapping` 表将其转为程序内部小写命名。

**为什么：** `parse_env_file` 是一个泛用的 .env 解析器，不应包含业务逻辑。`load_env_config` 集中管理所有 type coercion（int/float/bool），一处转型失败不会影响其他配置项。

### 规则 2.2：类型转型失败不阻断

```python
if cfg_key in ("requests_per_minute", "max_retries", ...):
    try:
        value = int(value)
    except (ValueError, TypeError):
        logger.warning(...)
        continue  # 跳过此项，使用后续默认值
```

**为什么：** 一个配置项损坏（如用户手写 `MAX_RETRIES=abc`）不应导致整个配置加载失败。跳过坏项、使用默认值、打 warning 日志，让用户在日志中发现并修复。

### 规则 2.3：新增配置项需要三个步骤

1. `key_mapping` 中添加 `"ENV_KEY": "internal_key"` 映射和类型转型
2. `get_*_config()` 中添加带默认值的返回
3. `save_env_file()` 调用处添加持久化写入

**为什么：** 缺少任何一步，配置都会在重启后丢失（写入但未保存）或无法读取（保存但未加载）。

## 三、save_env_file 原子性

### 规则 3.1：先写 tmp 再 replace

```python
tmp_path = env_path.with_suffix(".env.tmp")
tmp_path.write_text("...")
tmp_path.replace(env_path)
```

**为什么：** 如果直接写原文件，写入过程中程序崩溃会导致 .env 文件半损坏。`.tmp` → `.replace()` 在文件系统层面是原子的（NTFS 支持），要么全部写入成功，要么原文件不变。

## 四、日志系统设计

### 规则 4.1：ModuleFilter 前缀路由

日志路由基于 logger name，不基于模块文件路径。每个文件使用 `logger = logging.getLogger(__name__)`。`setup_logging()` 中的 `ModuleFilter` 按 `startswith` 前缀分派到不同文件。

**为什么：** `__name__` 是 Python 约定，与包结构天然一致。按前缀分派可以精确控制"哪些日志进哪个文件"，如 `src.business` 前缀全部进 `business/business.log`，`src.scraper.ai_` 前缀进 `ai_batch.log`。

### 规则 4.2：setup_logging 幂等性

`setup_logging()` 只移除并重建带有项目内部标记的 Handler，保留 pytest、宿主程序等外部 Handler；重复调用时会按新的级别和文件开关重新生效。

**为什么：** 仅判断 `root.handlers` 会误把外部 Handler 当成项目已初始化，导致文件日志无法创建；只管理自身 Handler 可以避免重复，同时不破坏宿主环境的日志采集。

### 规则 4.3：子进程日志分开存放

```python
("subprocess/stdout.log", ["subprocess.stdout"], None),
("subprocess/stderr.log", ["subprocess.stderr"], None),
```

**为什么：** 子进程的输出和主进程日志混在一起会干扰问题排查。stdout/stderr 分开更容易定位子进程崩溃的精确位置。

### 规则 4.4：异常必须写日志

所有 `except:` 块必须包含 `logger.error()` 调用，不允许 `except: pass`。非关键异常使用 `logger.warning()`，关键异常使用 `logger.exception()` 或 `logger.error("...")` + `logger.debug(traceback.format_exc())`。

**为什么：** 静默吞异常是调试灾难——问题只在特定条件下复现，而日志中没有留下任何线索。

### 规则 4.5：QProcess 子进程不直接轮转共享日志

主进程启动的 QProcess 子进程设置 `MJS_QPROCESS_CHILD=1`。子进程只输出控制台日志，由父进程写入 `subprocess/stdout.log` 和 `subprocess/stderr.log`，不直接打开主进程的 `RotatingFileHandler`。

**为什么：** `RotatingFileHandler` 不提供跨进程轮转协调；多个 Windows 进程同时写入和重命名同一文件可能产生文件占用冲突或备份错乱。
