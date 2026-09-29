# -*- coding: utf-8 -*-
"""doc2md —— PDF / Word 讲义 → Markdown / HTML，保留原格式（公式转 LaTeX），不做翻译。

模块职责：
    parsers/   各格式解析器，统一产出 Markdown
    postprocess.py  Markdown 清洗（公式定界符统一、噪声清理）
    render.py   Markdown → 独立 HTML（MathJax 渲染公式）
    config.py   全局配置（模型源、目录约定）
"""
__version__ = "0.1.0"
