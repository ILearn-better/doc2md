# -*- coding: utf-8 -*-
"""PDF → Markdown。

走 PaddleOCR 的 PP-StructureV3 产线：版面分析（标题/正文/表格/公式/图片）
+ 公式识别（输出 LaTeX）+ 表格识别（输出 HTML/Markdown 表），逐页处理后合并。

这是 PDF 路线能保住「数学讲义格式」的关键：PDF 里的公式是矢量图形或
私有字体，直接抽文本必然乱码，必须靠公式识别模型还原成 LaTeX。
"""
import shutil
import time
from pathlib import Path

from ..config import IMAGE_DIR_NAME

# 需要识别的版面元素（关掉不需要的以减少耗时）
#
# enable_mkldnn=False 是必须的：PaddlePaddle 3.3+ 在 Windows / CPU 上
# 「PIR 新执行器 + oneDNN」组合存在已知缺陷，会抛
#   NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support
#   [pir::ArrayAttribute<DoubleAttribute>] (onednn_instruction.cc:118)
# 实测崩溃发生在**版面检测模型**（RT-DETR）上，也就是说只要开了 oneDNN，
# 第一个模型就直接挂掉（0.3 秒）。关掉 oneDNN 走标准 CPU 内核即可
# （PaddlePaddle/PaddleOCR#17539、#17947）。config.py 里另设
# FLAGS_use_onednn=0 作为双重保险。
#
# 试过但**不推荐**的两条路（都实测过）：
#   1) FLAGS_enable_pir_api=0 退回旧执行器 —— 3.4.0 已移除旧执行器，flag 被忽略，照样崩。
#   2) 降级 paddlepaddle==3.2.2 开 oneDNN —— 确实不再崩溃，但公式密集页
#      7 分钟仍未出结果（CPU 时间 1335 秒、内存 5.7GB），没有实用价值。
# 结论：CPU 上 PDF 路线慢的根因是「公式识别模型本身是重 transformer」，
#      不是 oneDNN 开关能解决的。提速请用 --profile fast / --no-tables。
_BASE_KWARGS = dict(
    device="cpu",
    enable_mkldnn=False,
    use_doc_orientation_classify=False,   # 不自动纠正扫描方向
    use_doc_unwarping=False,              # 不做畸变校正（电子讲义本来就正）
    use_textline_orientation=False,       # 不做文本行角度分类
    use_formula_recognition=True,         # ★ 公式 → LaTeX
    use_table_recognition=True,           # ★ 表格 → HTML
    use_chart_recognition=False,          # 图表转数据表，讲义用不上
    use_seal_recognition=False,           # 印章识别，讲义用不上
)

# 速度档位。CPU 上默认档（server 版 OCR + L 版公式模型）精度最好但很慢，
# 公式密集的页面单页可能要几分钟；fast 档换小模型，速度快 3~5 倍，
# 代价是识别精度下降（题目文字仍够用，复杂公式建议用默认档）。
PROFILES = {
    "default": {},
    "fast": dict(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="PP-OCRv5_mobile_rec",
        formula_recognition_model_name="PP-FormulaNet_plus-S",
    ),
}


def _pipeline_kwargs(profile="default", use_formula=True, use_table=True):
    if profile not in PROFILES:
        raise ValueError("未知档位：%s（可选 %s）" % (profile, "/".join(PROFILES)))
    kw = dict(_BASE_KWARGS)
    kw.update(PROFILES[profile])
    # 关掉子模型是 CPU 上最有效的提速手段：表格识别会连带加载 5 个模型
    # （表格分类 + 有线/无线表结构 + 两个单元格检测），纯文字讲义完全用不上。
    if not use_formula:
        kw["use_formula_recognition"] = False
    if not use_table:
        kw["use_table_recognition"] = False
    return kw


# 进程内复用产线：模型加载要 40~60 秒，批处理多个文件时绝不能反复初始化
_PIPELINES = {}


def _build_pipeline(profile="default", use_formula=True, use_table=True):
    key = (profile, bool(use_formula), bool(use_table))
    if key not in _PIPELINES:
        from paddleocr import PPStructureV3
        _PIPELINES[key] = PPStructureV3(
            **_pipeline_kwargs(profile, use_formula, use_table)
        )
    return _PIPELINES[key]


def convert(src, out_dir, page_range=None, profile="default",
            use_formula=True, use_table=True, log=print):
    """把 PDF 转成 markdown。

    :param src: PDF 路径
    :param out_dir: 输出目录
    :param page_range: 形如 (1, 3) 的页区间（1 起，含两端），None 表示全部
    :param profile: 速度档位，见 PROFILES
    :param use_formula: 是否做公式识别（关掉可显著提速，但公式会退化成图片）
    :param use_table: 是否做表格识别（讲义没有表格时关掉，省 5 个模型）
    :param log: 日志回调
    :return: {"markdown": Path, "media_dir": Path|None, "pages": int}
    """
    src = Path(src).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_dir = out_dir / "_raw"
    if raw_dir.exists():
        shutil.rmtree(raw_dir, ignore_errors=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    key = (profile, bool(use_formula), bool(use_table))
    if key not in _PIPELINES:
        log("加载 PP-StructureV3 产线（首次运行会自动下载模型，约 1GB，请耐心等待）…")
    pipeline = _build_pipeline(profile, use_formula, use_table)

    # 页范围用「先把指定页抽成临时 PDF」实现，而不是给 predict 传 page_indexes：
    # PPStructureV3.predict 的签名里**没有** page_indexes（会被 **kwargs 静默吞掉），
    # 传了等于没传，整篇照样跑完 —— 这个坑踩过一次，别再传。
    work_pdf = src
    page_labels = None
    if page_range:
        work_pdf, page_labels = _extract_pages(src, page_range, raw_dir, log)

    if page_labels:
        rng = ("%d" % page_labels[0]) if len(page_labels) == 1 \
            else ("%d-%d" % (page_labels[0], page_labels[-1]))
        log("开始解析：%s（第 %s 页）" % (src.name, rng))
    else:
        log("开始解析：%s（全部页）" % src.name)
    t_all = time.time()

    # 必须用 predict_iter：predict() 内部是 list(predict_iter(...))，
    # 会把 15 页一次收干，长任务全程没有任何输出。predict_iter 才是逐页产出。
    pages = []
    t_prev = time.time()
    for res in pipeline.predict_iter(str(work_pdf)):
        infer = time.time() - t_prev          # 从上一页结束到本页产出 = 本页推理耗时
        i = len(pages) + 1
        page_dir = raw_dir / ("p%03d" % i)
        page_dir.mkdir(parents=True, exist_ok=True)
        try:
            res.save_to_markdown(save_path=str(page_dir))
        except TypeError:
            # 旧版本签名差异兜底
            res.save_to_markdown(str(page_dir))
        try:
            res.save_to_json(save_path=str(page_dir))
        except Exception:
            pass
        mds = sorted(page_dir.glob("*.md"))
        pages.append(mds[0].read_text(encoding="utf-8", errors="ignore") if mds else "")
        label = page_labels[i - 1] if page_labels else i
        log("  第 %s 页完成（推理 %.1fs）" % (label, infer))
        t_prev = time.time()

    if pages:
        total = time.time() - t_all
        log("  共 %d 页，推理合计 %.1fs（页均 %.1fs）" % (len(pages), total, total / len(pages)))

    merged = "\n\n".join(pages)
    md_path = out_dir / (src.stem + ".md")
    md_path.write_text(merged, encoding="utf-8")

    media_dir = _collect_images(raw_dir, out_dir, md_path, log)
    return {"markdown": md_path, "media_dir": media_dir, "pages": len(pages)}


def _extract_pages(src, page_range, tmp_dir, log):
    """把 src 的 page_range（1 起、含两端）抽成一个临时 PDF。

    :return: (临时 PDF 路径, 对应的 1 起原始页码列表)
    """
    try:
        import pymupdf as fitz          # 新版包名（fitz 这个名字已被标记废弃）
    except ImportError:                 # 老版本 pymupdf 只有 fitz
        import fitz

    a, b = page_range
    doc = fitz.open(str(src))
    try:
        n = doc.page_count
        a = max(1, a)
        b = min(b, n)
        if a > b:
            raise ValueError("页码范围 %s 超出 PDF 总页数 %d" % (page_range, n))
        if a != page_range[0] or b != page_range[1]:
            log("  页码范围已收敛到 %d-%d（原 PDF 共 %d 页）" % (a, b, n))
        out = fitz.open()
        out.insert_pdf(doc, from_page=a - 1, to_page=b - 1)
        tmp = tmp_dir / "_pages.pdf"
        out.save(str(tmp))
        out.close()
    finally:
        doc.close()
    return tmp, list(range(a, b + 1))


def _collect_images(raw_dir, out_dir, md_path, log):
    """把各页抽出的图片汇总到 assets/，并改写 markdown 里的引用路径。"""
    target = out_dir / IMAGE_DIR_NAME
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    moved = 0
    for img in raw_dir.rglob("*"):
        if img.is_file() and img.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
            dest = target / img.name
            k = 1
            while dest.exists():
                dest = target / ("%s_%d%s" % (img.stem, k, img.suffix))
                k += 1
            shutil.copy2(img, dest)
            moved += 1

    text = md_path.read_text(encoding="utf-8", errors="ignore")
    for pat in ("imgs/", "images/", "img/"):
        text = text.replace("(" + pat, "(" + IMAGE_DIR_NAME + "/")
    md_path.write_text(text, encoding="utf-8")

    shutil.rmtree(raw_dir, ignore_errors=True)
    log("  汇总图片 %d 张" % moved)
    return target if moved else None
