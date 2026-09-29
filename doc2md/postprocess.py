# -*- coding: utf-8 -*-
"""Markdown 后处理。

目标不是"美化"，而是让输出对下游入库更友好：
  * 公式定界符统一成 $...$ / $$...$$（MathJax、KaTeX、大部分题库系统都认这套）
  * 清掉 pandoc / OCR 产生的噪声标记
  * 规整空白，保证"一段一行"，方便按题号切分
"""
import re

_INLINE_MATH = re.compile(r"\\\((.+?)\\\)")
_DISPLAY_MATH = re.compile(r"\\\[(.+?)\\\]", re.S)
# pandoc 输出的数学写法：行内 $`...`$，行间 ``` math ... ```
_PANDOC_INLINE_MATH = re.compile(r"\$`([^`]*?)`\$")
_PANDOC_MATH_FENCE = re.compile(r"^[ \t]*```[ \t]*math[ \t]*\r?\n(.*?)\r?\n[ \t]*```[ \t]*$",
                                re.M | re.S)
_TIGHTLIST = re.compile(r"^\\tightlist\s*$", re.M)
_BLANK_RUN = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")
# pandoc 用行尾单个 \ 表示「硬换行」。转成 HTML 时 python-markdown 不认，
# 会渲染成字面反斜杠，所以去掉。负向后顾避免误伤 LaTeX 的 \\（矩阵换行）。
_TRAILING_ESCAPE = re.compile(r"(?<!\\)\\[ \t]*$", re.M)
# pandoc 在紧凑列表项之间插入的分隔注释，纯噪声
_PANDOC_SEP = re.compile(r"^[ \t]*<!--[ \t]*-->[ \t]*$", re.M)
# OCR 常见噪声：孤立的分页符/页码行
_PAGE_NOISE = re.compile(r"^\s*(?:第\s*\d+\s*页|Page\s+\d+\s*(?:of\s*\d+)?)\s*$", re.M)

# ---------------------------------------------------------------------------
# 题号/小标题提升（可选）
# Word 讲义常用「加粗段落」代替标题样式，pandoc 只能还原成 **加粗**。
# 下面这套规则把「整行加粗 + 形如题号」的行提升为 ### 标题，便于后续按题切分。
# 只做保守匹配，默认关闭，用 --promote-headings 打开。
# ---------------------------------------------------------------------------
_FULL_BOLD = re.compile(r"^(?:\*\*|__)(.+?)(?:\*\*|__)$")
_HEAD_HINTS = (
    re.compile(r"^第\s*\d+\s*[题节讲]"),
    re.compile(r"^参考(答案|解析|解答)"),
    re.compile(r"^[一二三四五六七八九十]+\s*[、.]"),
    re.compile(r"^\d+\s*[、.]\s*\S"),
    re.compile(r"^(练习|习题|作业|例题|真题)\s*\d*"),
)
_HEAD_MAX_LEN = 40


def clean(text):
    """清洗 markdown 文本。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # pandoc 的数学写法 → 通用 $...$ / $$...$$
    text = _PANDOC_MATH_FENCE.sub(
        lambda m: "\n$$\n" + m.group(1).strip() + "\n$$\n", text)
    text = _PANDOC_INLINE_MATH.sub(lambda m: "$" + m.group(1).strip() + "$", text)

    # 公式定界符统一：\( \) → $ $ ，\[ \] → $$ $$
    text = _INLINE_MATH.sub(lambda m: "$" + m.group(1).strip() + "$", text)
    text = _DISPLAY_MATH.sub(lambda m: "\n$$\n" + m.group(1).strip() + "\n$$\n", text)

    text = _TIGHTLIST.sub("", text)
    text = _PAGE_NOISE.sub("", text)
    text = _PANDOC_SEP.sub("", text)
    text = _TRAILING_ESCAPE.sub("", text)

    text = _TRAILING_WS.sub("\n", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip() + "\n"


def promote_headings(text):
    """把「整行加粗的题号行」提升为 ### 标题，便于按题切分入库。"""
    out = []
    for line in text.split("\n"):
        s = line.strip()
        m = _FULL_BOLD.match(s)
        if m:
            inner = m.group(1).strip()
            if len(inner) <= _HEAD_MAX_LEN and any(p.match(inner) for p in _HEAD_HINTS):
                out.append("### " + inner)
                continue
        out.append(line)
    return "\n".join(out)


def outline(text):
    """粗略统计文档结构，便于确认解析是否正常。"""
    lines = text.split("\n")
    headings = [ln for ln in lines if ln.startswith("#")]
    formulas = len(re.findall(r"\$\$.+?\$\$", text, re.S)) + len(re.findall(r"\$[^$\n]+?\$", text))
    images = len(re.findall(r"!\[[^\]]*\]\([^)]+\)", text)) + \
        len(re.findall(r"<img\b", text, re.I))
    tables = len(re.findall(r"^\|.*\|\s*$", text, re.M))
    return {
        "lines": len(lines),
        "chars": len(text),
        "headings": len(headings),
        "formulas": formulas,
        "images": images,
        "table_rows": tables,
    }
