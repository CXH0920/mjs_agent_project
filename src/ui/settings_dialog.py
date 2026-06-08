"""

名将杀 Agent - API 配置对话框



提供图形界面编辑 config.env 中的 API 配置项。

支持读取当前配置、原子写入、新建配置文件。

"""



from __future__ import annotations



import logging

import os

from pathlib import Path

from typing import Optional



from PySide6.QtWidgets import (

    QDialog,

    QDialogButtonBox,

    QFormLayout,

    QHBoxLayout,

    QLabel,

    QLineEdit,

    QMessageBox,

    QSpinBox,

    QVBoxLayout,

)

from PySide6.QtCore import Qt



logger = logging.getLogger(__name__)



# 项目根目录

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_ENV_FILE = PROJECT_ROOT / "config.env"



# 配置字段定义：(标签, 环境变量键, 控件类型, 默认值, 最小值, 最大值)

TEXT_FIELDS = [

    ("API Key", "DEEPSEEK_API_KEY", "", None, None),

    ("API URL", "DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions", None, None),

    ("模型名称", "DEEPSEEK_MODEL", "deepseek-v4-pro", None, None),

]



SPIN_FIELDS = [

    ("每分钟请求数", "REQUESTS_PER_MINUTE", 30, 1, 120),

    ("HTTP 超时(秒)", "HTTP_TIMEOUT", 300, 10, 600),

    ("最大重试次数", "MAX_RETRIES", 3, 0, 10),

]





def _parse_env_file(env_path: Path) -> dict[str, str]:

    """解析 .env 文件（精简版，避免依赖 ai_batch 模块）"""

    if not env_path.exists():

        return {}

    result = {}

    try:

        for line in env_path.read_text(encoding="utf-8").splitlines():

            stripped = line.strip()

            if not stripped or stripped.startswith("#"):

                continue

            if "=" not in stripped:

                continue

            key, _, value = stripped.partition("=")

            key = key.strip()

            value = value.strip().strip('"\'')

            if key:

                result[key] = value

    except Exception as e:

        logger.warning("解析 .env 文件失败: %s", e)

    return result





def _save_env_file(env_path: Path, data: dict[str, str]) -> None:

    """原子写入 .env 文件"""

    # 保留原文件中的注释和格式

    lines: list[str] = []

    if env_path.exists():

        for line in env_path.read_text(encoding="utf-8").splitlines():

            stripped = line.strip()

            if not stripped or stripped.startswith("#"):

                lines.append(line)

            else:

                key = stripped.split("=")[0].strip() if "=" in stripped else ""

                if key not in data:

                    lines.append(line)



    # 添加/更新配置项

    existing_keys = set()

    if env_path.exists():

        for line in env_path.read_text(encoding="utf-8").splitlines():

            stripped = line.strip()

            if stripped and not stripped.startswith("#") and "=" in stripped:

                key = stripped.split("=")[0].strip()

                existing_keys.add(key)



    for key in data:

        if key not in existing_keys:

            lines.append(f"{key}={data[key]}")



    # 更新已有的键值

    result_lines: list[str] = []

    for line in lines:

        stripped = line.strip()

        if not stripped.startswith("#") and "=" in stripped:

            key = stripped.split("=")[0].strip()

            if key in data:

                result_lines.append(f"{key}={data[key]}")

                # 标记已处理

                data.pop(key)

                continue

        result_lines.append(line)



    # 追加未写入的键

    for key, value in data.items():

        result_lines.append(f"{key}={value}")



    # 原子写入

    tmp_path = env_path.with_suffix(".env.tmp")

    tmp_path.write_text("\\n".join(result_lines) + "\\n", encoding="utf-8")

    tmp_path.replace(env_path)





class SettingsDialog(QDialog):

    """API 配置编辑对话框



    以表单形式编辑 config.env 中的所有配置项，支持新建和原子写入。

    """



    def __init__(self, env_path: Optional[Path] = None, parent=None):

        super().__init__(parent)

        self._env_path = env_path or DEFAULT_ENV_FILE

        self._text_widgets: dict[str, QLineEdit] = {}

        self._spin_widgets: dict[str, QSpinBox] = {}



        self.setWindowTitle("API 配置")

        self.setMinimumWidth(450)

        self._setup_ui()

        self._load_config()



    # ---------------------------------------------------------------

    # UI 构建

    # ---------------------------------------------------------------



    def _setup_ui(self) -> None:

        """构建对话框界面"""

        layout = QVBoxLayout(self)



        # 表单布局

        form = QFormLayout()

        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)



        # 文本输入字段

        for label, key, default, _, _ in TEXT_FIELDS:

            widget = QLineEdit()

            if key == "DEEPSEEK_API_KEY":

                widget.setEchoMode(QLineEdit.EchoMode.Password)

                widget.setPlaceholderText("输入 DeepSeek API Key")

            else:

                widget.setPlaceholderText(default or "")

            self._text_widgets[key] = widget

            form.addRow(f"{label}:", widget)



        # 数值输入字段

        for label, key, default, min_val, max_val in SPIN_FIELDS:

            widget = QSpinBox()

            widget.setRange(min_val, max_val)

            widget.setValue(default)

            self._spin_widgets[key] = widget

            form.addRow(f"{label}:", widget)



        layout.addLayout(form)



        # 文件路径提示

        hint = QLabel(f"配置文件: {self._env_path}")

        hint.setStyleSheet("color: gray; font-size: 11px;")

        hint.setWordWrap(True)

        layout.addWidget(hint)



        # 按钮

        buttons = QDialogButtonBox(

            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel

        )

        buttons.accepted.connect(self._on_save)

        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)



    # ---------------------------------------------------------------

    # 加载 / 保存

    # ---------------------------------------------------------------



    def _load_config(self) -> None:

        """从 config.env 加载当前配置到表单"""

        data = _parse_env_file(self._env_path)



        for key, widget in self._text_widgets.items():

            if key in data:

                widget.setText(data[key])



        for key, widget in self._spin_widgets.items():

            if key in data:

                try:

                    widget.setValue(int(data[key]))

                except (ValueError, TypeError):

                    pass



    def _on_save(self) -> None:

        """保存配置到 config.env"""

        # 收集数据

        data: dict[str, str] = {}

        for key, widget in self._text_widgets.items():

            value = widget.text().strip()

            if value:

                data[key] = value

        for key, widget in self._spin_widgets.items():

            data[key] = str(widget.value())



        try:

            # 确保目录存在

            self._env_path.parent.mkdir(parents=True, exist_ok=True)

            _save_env_file(self._env_path, data)

            QMessageBox.information(self, "保存成功", f"配置已保存到:\\n{self._env_path}")

            self.accept()

        except Exception as e:

            logger.exception("保存配置失败")

            QMessageBox.critical(self, "保存失败", f"无法写入配置文件:\\n{e}")

