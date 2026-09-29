# -*- coding: utf-8 -*-
"""各格式解析器。重型依赖（pypandoc / paddleocr）都在函数内部按需导入，
保证只装用得到的那部分也能跑（例如只用 docx 路线时不装 paddle）。"""
from . import docx_parser, pdf_parser  # noqa: F401

__all__ = ["docx_parser", "pdf_parser"]
