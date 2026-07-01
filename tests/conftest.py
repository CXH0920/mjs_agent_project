"""pytest 配置：将项目根目录加入 sys.path，统一使用 `from src.xxx` 导入模式"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
