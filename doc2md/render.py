# -*- coding: utf-8 -*-
"""Markdown → 独立 HTML。

公式交给 MathJax 在浏览器端渲染：markdown 库不认识 LaTeX，
先把 $...$ / $$...$$ 抠出来换成占位符，转完 HTML 再还原成 \\(...\\) / \\[...\\]，
这样公式里的 < > & 等字符不会被当成 HTML 破坏掉。
"""
import base64
import html as _html
import mimetypes
import re
from pathlib import Path
from string import Template

from .config import MATHJAX_URL

_MATH = re.compile(r"\$\$.+?\$\$|\$[^$\n]+?\$", re.S)
_PLACEHOLDER = re.compile(r"@@MATH(\d+)@@")
_IMG_TAG = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")')

_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title>
<script>
window.MathJax = {
  tex: { inlineMath: [['\\\\(', '\\\\)']], displayMath: [['\\\\[', '\\\\]']] },
  svg: { fontCache: 'global' }
};
</script>
<script id="MathJax-script" async src="$mathjax"></script>
<style>
  :root { --fg:#1a1a1a; --muted:#666; --line:#e3e3e3; --bg:#fff; }
  * { box-sizing: border-box; }
  body {
    margin: 0 auto; padding: 48px 32px 96px; max-width: 900px;
    font-family: -apple-system, "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 16px; line-height: 1.75; color: var(--fg); background: var(--bg);
  }
  h1, h2, h3, h4 { line-height: 1.35; margin: 1.6em 0 .6em; font-weight: 600; }
  h1 { font-size: 1.9em; border-bottom: 2px solid var(--line); padding-bottom: .3em; }
  h2 { font-size: 1.45em; border-bottom: 1px solid var(--line); padding-bottom: .25em; }
  h3 { font-size: 1.2em; }
  p { margin: .8em 0; }
  img { max-width: 100%; height: auto; display: block; margin: 1em auto; }
  table { border-collapse: collapse; margin: 1em 0; width: 100%; }
  th, td { border: 1px solid var(--line); padding: 6px 10px; text-align: left; }
  th { background: #fafafa; font-weight: 600; }
  code { background: #f5f5f5; padding: .15em .35em; border-radius: 3px;
         font-family: Consolas, "Courier New", monospace; font-size: .92em; }
  pre { background: #f7f7f7; padding: 12px 14px; border-radius: 6px; overflow-x: auto; }
  pre code { background: none; padding: 0; }
  blockquote { margin: 1em 0; padding: .2em 1em; color: var(--muted);
               border-left: 3px solid var(--line); }
  hr { border: none; border-top: 1px solid var(--line); margin: 2em 0; }
  @media print { body { padding: 0; max-width: none; font-size: 12pt; } }
</style>
</head>
<body>
$body
</body>
</html>
""")


def _protect_math(md_text):
    store = []

    def repl(m):
        store.append(m.group(0))
        return "@@MATH%d@@" % (len(store) - 1)

    return _MATH.sub(repl, md_text), store


def _restore_math(html_text, store):
    def repl(m):
        raw = store[int(m.group(1))]
        if raw.startswith("$$"):
            inner = raw[2:-2].strip()
            return "\\[" + _html.escape(inner, quote=False) + "\\]"
        inner = raw[1:-1].strip()
        return "\\(" + _html.escape(inner, quote=False) + "\\)"

    return _PLACEHOLDER.sub(repl, html_text)


def _inline_images(html_text, base_dir):
    """把本地图片转成 base64 内联，生成"单文件可发"的 HTML。"""
    def repl(m):
        src = m.group(2)
        if src.startswith(("http://", "https://", "data:")):
            return m.group(0)
        p = Path(src)
        if not p.is_absolute():
            p = base_dir / src
        if not p.is_file():
            return m.group(0)
        mime = mimetypes.guess_type(str(p))[0] or "image/png"
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        return "%sdata:%s;base64,%s%s" % (m.group(1), mime, b64, m.group(3))

    return _IMG_TAG.sub(repl, html_text)


def to_html(md_text, title="", base_dir=None, embed_images=False, mathjax_url=MATHJAX_URL):
    """把 markdown 文本渲染为完整 HTML 文档字符串。"""
    import markdown as md_lib

    protected, store = _protect_math(md_text)
    body = md_lib.markdown(
        protected,
        extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
    )
    body = _restore_math(body, store)

    if embed_images and base_dir:
        body = _inline_images(body, Path(base_dir))

    return _TEMPLATE.substitute(title=_html.escape(title or "Document"),
                                mathjax=mathjax_url, body=body)


def write_html(md_path, html_path=None, embed_images=False, title=None):
    """读 markdown 文件并写出 HTML，返回 HTML 路径。"""
    md_path = Path(md_path).resolve()
    if html_path is None:
        html_path = md_path.with_suffix(".html")
    html_path = Path(html_path)
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    doc = to_html(text, title=title or md_path.stem,
                  base_dir=md_path.parent, embed_images=embed_images)
    html_path.write_text(doc, encoding="utf-8")
    return html_path
