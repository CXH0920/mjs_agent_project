"""模拟器设备值对象（自原单文件探测模块拆包，多品牌连接支持）。

独立成模块而非定义在包 __init__：品牌子模块需要引用它，定义在 __init__
会与 __init__ 导入品牌模块形成循环；models 不 import 任何兄弟模块，
处于依赖最底层。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EmulatorDeviceInfo:
    """描述一个模拟器实例（品牌无关；字段沿用原 MuMuDeviceInfo）。"""

    index: str          # 品牌工具中的实例索引
    name: str           # 实例名称
    adb_port: int       # ADB 端口
    is_running: bool    # 是否正在运行
    is_main: bool = False
