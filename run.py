# -*- coding: utf-8 -*-
"""doc2md 命令行入口。

用法示例：
    python run.py 讲义.docx                      # 转 Markdown + HTML
    python run.py 讲义.pdf -o out/ --format md   # 只要 Markdown
    python run.py 讲义.pdf --pages 1-3           # 只解析前 3 页
    python run.py 讲义.pdf --embed-images        # HTML 图片内联，单文件可发
    python run.py samples/*.docx -o out/         # 批处理（同一进程，模型只加载一次）

设计取舍：不引入"中间数据模型"，两个解析器都直接产出 Markdown（公式已是 LaTeX），
统一做后处理与渲染。数学讲义后续要按题号切分入库，在 Markdown 上做规则切分最直观。
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # 控制台直连时保持原生编码；重定向/管道时统一 UTF-8，避免中文乱码
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from doc2md import config, postprocess, render, splitter   # noqa: E402
from doc2md.parsers import docx_parser, pdf_parser         # noqa: E402


def parse_pages(s):
    """'1-5' → (1,5)；'3' → (3,3)；None → None"""
    if not s:
        return None
    if "-" in s:
        a, b = s.split("-", 1)
        return (int(a), int(b))
    n = int(s)
    return (n, n)


def build_parser():
    ap = argparse.ArgumentParser(
        prog="doc2md",
        description="把 PDF / Word 讲义解析成 Markdown 与 HTML，保留原格式（公式转 LaTeX），不做翻译。",
    )
    ap.add_argument("input", nargs="+", help="输入文件（.pdf / .docx），可给多个做批处理")
    ap.add_argument("-o", "--out", help="输出目录；单个文件时默认 <输入文件名>_out/，多个文件时按文件名建子目录")
    ap.add_argument("--format", default="md,html", help="输出格式，逗号分隔：md,html（默认两者都出）")
    ap.add_argument("--pages", help="仅解析指定页（仅 PDF 有效），如 1-5 或 3")
    ap.add_argument("--profile", default="default", choices=["default", "fast"],
                    help="PDF 识别档位：default 精度优先（server OCR + L 版公式模型，慢）；"
                         "fast 速度优先（mobile OCR + S 版公式模型，快 3~5 倍，精度略降）")
    ap.add_argument("--no-formula", action="store_true",
                    help="PDF：关闭公式识别（公式会退化成图片，但明显提速）")
    ap.add_argument("--no-tables", action="store_true",
                    help="PDF：关闭表格识别（讲义没有表格时用，省掉 5 个子模型）")
    ap.add_argument("--embed-images", action="store_true",
                    help="HTML 中把图片内联为 base64，生成单文件可分享的 HTML")
    ap.add_argument("--promote-headings", action="store_true",
                    help="把「整行加粗的题号」（如 **第 1 题**）提升为 ### 标题，便于按题切分入库")
    ap.add_argument("--split", action="store_true",
                    help="按题号切题，输出 questions.json / questions.csv（入题库用）")
    ap.add_argument("--split-files", action="store_true",
                    help="配合 --split，额外为每道题输出单独 .md 文件")
    return ap


def process_one(src, out_dir, formats, args):
    """处理单个文件，返回 (成功?, 统计字典)。"""
    suffix = src.suffix.lower()
    if suffix == ".docx":
        if args.pages:
            print("[提示] --pages 对 Word 无效，已忽略。")
        result = docx_parser.convert(src, out_dir)
    elif suffix == ".pdf":
        if args.no_formula or args.no_tables:
            offs = []
            if args.no_formula:
                offs.append("公式识别")
            if args.no_tables:
                offs.append("表格识别")
            print("[提示] 已关闭：%s" % "、".join(offs))
        result = pdf_parser.convert(src, out_dir, page_range=parse_pages(args.pages),
                                    profile=args.profile,
                                    use_formula=not args.no_formula,
                                    use_table=not args.no_tables)
    else:
        print("[错误] 暂不支持的类型：%s（目前支持 .pdf / .docx）" % suffix)
        return False, None

    md_path = Path(result["markdown"])
    text = postprocess.clean(md_path.read_text(encoding="utf-8", errors="ignore"))
    if args.promote_headings:
        text = postprocess.promote_headings(text)
    md_path.write_text(text, encoding="utf-8")

    print("-" * 64)
    print("Markdown：%s" % md_path)
    if "html" in formats:
        html_path = render.write_html(md_path, embed_images=args.embed_images)
        print("HTML    ：%s" % html_path)
    if result.get("media_dir"):
        print("图片    ：%s" % result["media_dir"])

    stats = postprocess.outline(text)
    print("-" * 64)
    print("统计：%d 字符 / %d 行 | 标题 %d | 公式 %d | 图片 %d | 表格行 %d"
          % (stats["chars"], stats["lines"], stats["headings"],
             stats["formulas"], stats["images"], stats["table_rows"]))

    if args.split:
        doc = splitter.split(text, source=src.name)
        json_path, csv_path = splitter.write_outputs(doc, out_dir,
                                                     files=args.split_files)
        s = doc["stats"]
        print("切题：%d 道（可信 %d / 待复核 %d）| 带答案 %d | 大节 %d"
              % (s["count"], s["high"], s["medium"] + s["low"],
                 s["with_answer"], s["sections"]))
        print("题目    ：%s" % json_path)
        print("表格    ：%s" % csv_path)
        if s["medium"] + s["low"]:
            print("[提示] 有 %d 道题需要人工复核（questions.csv 的 flags/confidence 列）"
                  % (s["medium"] + s["low"]))
        stats["questions"] = s["count"]
    return True, stats


def main(argv=None):
    args = build_parser().parse_args(argv)

    sources = [Path(p).expanduser().resolve() for p in args.input]
    missing = [p for p in sources if not p.is_file()]
    if missing:
        for p in missing:
            print("[错误] 文件不存在：%s" % p)
        return 2

    formats = {f.strip().lower() for f in args.format.split(",") if f.strip()}
    base_out = Path(args.out).expanduser().resolve() if args.out else None
    multi = len(sources) > 1

    t0 = time.time()
    ok = 0
    for i, src in enumerate(sources, 1):
        if base_out is None:
            out_dir = config.default_out_dir(src)
        elif multi:
            out_dir = base_out / src.stem
        else:
            out_dir = base_out

        print("=" * 64)
        print("[%d/%d] 输入：%s" % (i, len(sources), src))
        print("输出：%s" % out_dir)
        print("=" * 64)

        try:
            good, _ = process_one(src, out_dir, formats, args)
        except Exception as e:                      # 批处理不因单文件失败而中断
            print("[错误] 处理失败：%r" % (e,))
            good = False
        if good:
            ok += 1

    print("=" * 64)
    print("完成 %d/%d 个文件，总耗时 %.1f 秒" % (ok, len(sources), time.time() - t0))
    print("=" * 64)
    return 0 if ok == len(sources) else 1


if __name__ == "__main__":
    raise SystemExit(main())
