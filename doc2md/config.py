# -*- coding: utf-8 -*-
"""全局配置。

集中管理模型来源、目录约定等，避免散落在各解析器里。
"""
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# PaddleOCR 模型下载源
#   huggingface / modelscope / bos / aistudio
# 国内网络推荐 modelscope 或 bos（bos 为百度对象存储，通常最快）
# 模型缓存在 ~/.paddlex/official_models/，首次运行自动下载，之后离线可用。
# ---------------------------------------------------------------------------
os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")

# 跳过「连通性探测」环节，直接按上面的源下载，省掉几秒等待与额外请求
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

# ---------------------------------------------------------------------------
# Windows / CPU 兼容性开关（重要）
# PaddlePaddle 3.3+ 的 PIR 新执行器与 oneDNN 后端的属性转换存在缺陷，CPU 推理
# 会抛 NotImplementedError(ConvertPirAttribute2RuntimeAttribute ...)。
# 关掉 oneDNN 走标准 CPU 内核。必须在本模块被导入前生效，所以放在 config 里。
# 参见 PaddleOCR issue #17539 / #17947。
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    os.environ.setdefault("FLAGS_use_onednn", "0")
    os.environ.setdefault("FLAGS_use_mkldnn", "0")

# 关闭 paddle 的部分冗余日志（不影响错误输出）
os.environ.setdefault("GLOG_minloglevel", "2")

# 输出目录约定
IMAGE_DIR_NAME = "assets"      # 图片资源子目录名
MD_SUFFIX = ".md"
HTML_SUFFIX = ".html"

# PDF 渲染分辨率（PP-StructureV3 内部会用更高分辨率做公式/表格识别）
PDF_RENDER_DPI = 200

# MathJax CDN（生成的 HTML 默认引用，便于公式直接查看）
MATHJAX_URL = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"


def default_out_dir(src: Path) -> Path:
    """未指定输出目录时，在源文件同级生成 <stem>_out/。"""
    src = Path(src)
    return src.parent / (src.stem + "_out")
