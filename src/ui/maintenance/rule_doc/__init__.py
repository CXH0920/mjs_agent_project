# -*- coding: utf-8 -*-
"""元规则维护面板的四个子页签（第二轮第 15 批审查拆分）。

文档状态（audit）/ 数据段差异（sync）/ 提案工作台（propose）/ 疑难登记
（pending）各自收敛为独立 QWidget，宿主 RuleDocPanel（rule_doc_panel.py）
只保留页签装配、脚本执行底座与跨页签编排。页签需要跑脚本或跨页联动时
经意图信号回宿主，自身不感知脚本存在。
"""
