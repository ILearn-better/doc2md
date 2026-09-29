# -*- coding: utf-8 -*-
"""Word(.docx) → Markdown。

走 pandoc 转换。docx 里的公式本身就是结构化的 OMML（Office Math Markup），
pandoc 能将其无损转成 LaTeX —— 这是 docx 相比 PDF 最大的优势，公式不需要 OCR。

要点：
  * 输出 GFM（GitHub Flavored Markdown），表格用 pipe table，公式用 $...$ / $$...$$
  * --wrap=none 避免 pandoc 按 72 列硬折行，保持段落完整
  * --extract-media 把内嵌图片导出到磁盘，再把目录名统一成 assets/
"""
import re
import shutil
from pathlib import Path

from ..config import IMAGE_DIR_NAME

# pandoc 的两种图片写法
# 注意 src 部分必须是 [^)]+?（允许空格）：pandoc 的 --extract-media 会写出
# 绝对路径，Windows 下如 F:\Program Files\... 含空格，用 [^)\s]+ 会直接失配，
# 导致行内小图（公式截图）保留绝对路径、没被收进 assets/。
_MD_IMG = re.compile(r"!\[([^\]]*)\]\(([^)]+?)(\s+\"[^\"]*\")?\)")
_HTML_IMG = re.compile(r"<img\b[^>]*?/?>", re.I)
_HTML_SRC = re.compile(r'src="([^"]+)"', re.I)


def convert(src, out_dir):
    """把 docx 转成 markdown。

    :param src: .docx 路径
    :param out_dir: 输出目录（不存在会创建）
    :return: {"markdown": Path, "media_dir": Path|None}
    """
    import pypandoc

    src = Path(src).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / (src.stem + ".md")
    raw_media = out_dir / "media"

    pypandoc.convert_file(
        str(src),
        to="gfm+tex_math_dollars+pipe_tables",
        format="docx",
        outputfile=str(md_path),
        extra_args=[
            "--wrap=none",
            "--markdown-headings=atx",
            "--extract-media=" + str(out_dir),
        ],
    )

    text = md_path.read_text(encoding="utf-8", errors="ignore")
    text = _normalize_media(text, raw_media, out_dir)
    md_path.write_text(text, encoding="utf-8")

    media_dir = out_dir / IMAGE_DIR_NAME
    has_media = media_dir.is_dir() and any(p.is_file() for p in media_dir.rglob("*"))
    return {"markdown": md_path, "media_dir": media_dir if has_media else None}


def _normalize_media(text, raw_media, out_dir):
    """把 pandoc 写出的图片引用统一成 assets/xxx.png 相对路径。

    pandoc 有两种写法，都要处理：
      * 普通图片 → markdown 语法 ![](media/xxx.png)
      * 带尺寸的图片 → 裸 HTML <img src="...media/xxx.png" style="width:..in" />
    这里统一转成 markdown 图片语法，保证 markdown 可移植（裸 HTML 不是所有
    下游解析器都认）。代价是 Word 里的显示尺寸丢失——由渲染端按版心自适应。
    """
    # 1) 裸 <img> 标签 → markdown 图片
    def _img_repl(m):
        tag = m.group(0)
        src_m = _HTML_SRC.search(tag)
        if not src_m:
            return tag
        rel = _to_assets(src_m.group(1))
        if rel is None:
            return tag
        base = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return "![%s](%s)" % (base, rel)

    text = _HTML_IMG.sub(_img_repl, text)

    # 2) markdown 图片语法 → 统一目录名
    def _md_repl(m):
        alt, src, tail = m.group(1), m.group(2), m.group(3) or ""
        rel = _to_assets(src)
        if rel is None:
            return m.group(0)
        if not alt:
            alt = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return "![%s](%s)%s" % (alt, rel, tail)

    text = _MD_IMG.sub(_md_repl, text)

    # 3) 目录 media/ → assets/
    target = out_dir / IMAGE_DIR_NAME
    if raw_media.is_dir():
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        try:
            raw_media.rename(target)
        except OSError:
            shutil.copytree(raw_media, target, dirs_exist_ok=True)
            shutil.rmtree(raw_media, ignore_errors=True)
    return text


def _to_assets(src):
    """把 pandoc 的图片路径（可能是绝对路径、反斜杠混用）映射成 assets/文件名。

    注意：绝不能对整个文本做反斜杠替换——那会把 LaTeX 的 \\frac 一起毁掉。
    只对图片 src 做。
    """
    s = src.replace("\\", "/")
    for key in ("/media/", "media/"):
        if key in s:
            rest = s.split(key, 1)[1]
            if rest:
                return IMAGE_DIR_NAME + "/" + rest.lstrip("/")
    return None
