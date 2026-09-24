# -*- coding: utf-8 -*-
"""vulture 白名单：框架回调符号，被引用即视为"使用"。

仅收录由框架（Qt / urllib）按名字回调的覆写方法——它们在代码里没有显式调用点，
不是死代码。与 vulture 命令一起扫描：
    python -m vulture src tests/vulture_whitelist.py \
        --min-confidence 60 --ignore-decorators "@field_validator,@model_validator"
pydantic 校验器（带参数装饰器）用 --ignore-decorators 处理，不放这里。
"""

redirect_request  # urllib.request.HTTPRedirectHandler 逐跳拦截重定向（crawler._NoRedirectHandler）
itemAt  # QLayout 抽象方法（widgets.FlowLayout）
expandingDirections  # QLayout 抽象方法（widgets.FlowLayout）
hasHeightForWidth  # QLayout 抽象方法（widgets.FlowLayout）
heightForWidth  # QLayout 抽象方法（widgets.FlowLayout）
mouseMoveEvent  # QWidget 事件回调（roi_selector）
paintEvent  # QWidget 事件回调（roi_selector）
