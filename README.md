# doc2md

把 **PDF / Word 讲义**解析成 **Markdown 和 HTML**，保留原格式、不做翻译。

面向的场景：**数学讲义 → 结构化文本 → 入题库**。所以这里最看重的不是"好看"，
而是**公式必须是可机读的 LaTeX**、结构（标题/题目/选项/表格）必须可切分。

---

## 两条链路的原理差异

| | Word (.docx) | PDF |
|---|---|---|
| 引擎 | pandoc | PaddleOCR PP-StructureV3 |
| 公式 | **无损**：docx 内部公式本就是 OMML 结构化数据，直接转 LaTeX | **需识别**：公式是矢量图形或私有字体，必须靠公式识别模型还原 |
| 表格 | pandoc 原生支持 | 表格识别模型输出 HTML → Markdown |
| 速度 | 秒级 | 逐页版面分析 + OCR，页均数秒（CPU） |
| 可靠性 | 高，几乎不需人工校对 | 取决于排版质量，扫描件需要 OCR |

**结论：能拿到 Word 原件就不要用 PDF。** PDF 路线是为只有 PDF 的场景兜底。

---

## 安装

```bash
# 1) 基础依赖
pip install pypandoc-binary python-docx markdown beautifulsoup4 lxml pymupdf \
    -i https://mirrors.aliyun.com/pypi/simple/

# 2) PaddleOCR（清华/阿里镜像同步不全，paddlepaddle 必须走官方 CPU 源）
pip install paddlepaddle -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install "paddlex[ocr]" paddleocr -i https://mirrors.aliyun.com/pypi/simple/
```

> 只用 Word 路线的话，第 2 步完全可以跳过 —— 解析器内部是按需导入的。

**实测可用组合**（Windows 11 / Python 3.12 / CPU）：

| 包 | 版本 |
|---|---|
| paddlepaddle | 3.4.0 |
| paddleocr | 3.7.0 |
| paddlex[ocr] | 3.7.2 |
| pypandoc-binary | 1.17（内置 pandoc 3.9） |

首次跑 PDF 会自动下载 PP-StructureV3 全套模型（12 个，约 1GB），缓存到
`~/.paddlex/official_models/`，之后离线可用。国内网络默认已设为 BOS 源。

### 两个装完才会踩到的坑

**① `PP-StructureV3 requires additional dependencies`**
`pip install paddleocr` 只带 `paddlex[ocr-core]`。必须补装完整依赖组：

```bash
pip install "paddlex[ocr]"
```

**② `NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support
[pir::ArrayAttribute<DoubleAttribute>] (onednn_instruction.cc)`**
PaddlePaddle 3.3+ 的 PIR 新执行器与 oneDNN 后端属性转换有缺陷，Windows/CPU 下
必现（PaddleOCR #17539 / #17947）。实测崩溃点是**版面检测模型（RT-DETR）**，
也就是只要开了 oneDNN，产线第一个模型就直接挂掉（0.3 秒）。
本项目已在 `config.py` 里设 `FLAGS_use_onednn=0` 并在 `pdf_parser.py` 里传
`enable_mkldnn=False` 双重规避，**开箱即可用**。

> 另外两条路都实测过，**都不推荐**：
> - `FLAGS_enable_pir_api=0` 想退回旧执行器 —— 3.4.0 已移除旧执行器，flag 直接
>   被忽略，照样崩。
> - 降级 `paddlepaddle==3.2.2` 开 oneDNN —— 确实**不再崩溃**了，但公式密集页
>   跑 7 分钟仍没出结果（CPU 时间 1335 秒、内存涨到 5.7GB）。
>
> **根因不在 oneDNN，而在公式识别模型本身是重 transformer。** CPU 上想提速，
> 用下面的 `--profile fast` / `--no-tables` / `--no-formula`，别折腾 paddle 版本。

**③ pip 装包时被安全软件/沙箱的「批量删除」拦截**
pip 清理缓存临时文件时会触发。加 `--no-cache-dir` 让它改走系统临时目录。

---

## 用法

```bash
python run.py 讲义.docx                        # 输出 Markdown + HTML
python run.py 讲义.pdf -o out/                 # 指定输出目录
python run.py 讲义.pdf --format md             # 只要 Markdown
python run.py 讲义.pdf --pages 1-3             # 只解析前 3 页（调试用）
python run.py 讲义.pdf --embed-images          # HTML 图片内联成单文件，方便直接发出去
python run.py 讲义.docx --split                # ★ 切题：额外输出 questions.json / questions.csv
python run.py 讲义.docx --split --split-files  # 切题并给每道题单独出一个 .md
python run.py 讲义.docx --promote-headings     # 把 **第 1 题** 这类加粗题号提升为 ### 标题
python run.py 讲义.pdf --profile fast          # 速度优先档（见下）
python run.py 讲义.pdf --no-tables             # 讲义没有表格时关掉表格识别，省 5 个子模型
python run.py 讲义.pdf --no-formula            # 纯文字讲义：关掉公式识别（公式会退化成图片）
python run.py samples/*.docx -o out/           # 批处理：同一进程，模型只加载一次
```

### `--profile`：速度档位（仅 PDF）

CPU 上默认档用的是 server 版 OCR + L 版公式模型，精度最好但很慢。`fast` 档换成
mobile OCR + S 版公式模型：

| 档位 | 文本检测/识别 | 公式识别 | 适合 |
|---|---|---|---|
| `default` | PP-OCRv5_server | PP-FormulaNet_plus-L | 归档、需要最准的公式 |
| `fast` | PP-OCRv5_mobile | PP-FormulaNet_plus-S | 批量初筛、以文字题为主 |

**实测耗时**（Attention 论文第 4 页，公式最密的一页，Windows / 12 核 CPU）：

| 配置 | 单页推理耗时 |
|---|---|
| `--profile default` | > 11 分钟（未跑完） |
| `--profile fast` | **46 秒** |
| `--profile fast` 整篇 15 页 | 627 秒（页均约 40 秒） |

> ⚠ **`fast` 档的公式会变脏，别拿去入题库。** S 版公式模型会把单词逐字空格化，
> 实测输出过 `\operatorname{S u b l a y e r}`、`\ t\ t{e}n t o n(Q,K,V)`（Attention）、
> `{M\ l t{t H e a d}(...)}}`（MultiHead）、`\bar{h=64}` 这类垃圾。版面、正文、表格
> 在 fast 档是好的（表格能正确输出成 HTML table），**只有公式退化**。
> 要干净的 LaTeX 只能用 `default` 档，而它在 CPU 上慢到不可用 —— 所以**有 Word
> 原件就用 Word 路线**，这是唯一的正解。

**提速的正确姿势**（按性价比排序）：先 `--no-tables`（表格识别会连带加载 5 个模型），
再 `--no-formula`（纯文字讲义），最后才考虑 `--profile fast`。

> 首批模型加载固定要 40~60 秒（一次进程只付一次），所以**批处理务必把多个文件
> 一次传给 run.py**，不要 for 循环一页起一个进程。

### `--promote-headings` 说明

Word 讲义普遍用「加粗段落」代替标题样式，pandoc 只能还原成 `**加粗**`。加这个
开关后，**整行加粗且形如题号**的行（`第 1 题`、`一、`、`参考答案`…）会提升为
`###` 标题，方便按题切分入库。默认关闭，因为它严格来说改变了原格式。

> `--split` 不依赖这个开关 —— 切题器自己就能识别 `**第 1 题**` 这类加粗题号。

---

## 切题（`--split`）：讲义 → 题库

入题库的正题。把 Markdown 按题号切成**一道题一条记录**，题干、选项、答案、解析
分字段，直接产出题库系统能吃的 JSON / CSV。

```bash
python run.py 讲义.docx --split
```

### 输出

```
<文件名>_out/
├── questions.json       # 主产物：结构化题目数组，UTF-8
├── questions.csv        # 同内容表格版（UTF-8-BOM，Excel 直接双击可开）
└── questions/           # 仅 --split-files 时生成
    ├── q001.md
    └── q002.md
```

### 字段

| 字段 | 说明 |
|---|---|
| `index` | 全局序号（1 起），跨大节连续编号 |
| `number` / `number_kind` | 文档里的原题号，及其写法类型（`第N题` / `N.` / `(N)`） |
| `section` / `section_index` | 所属大节（如 `一、选择题（每题 4 分）`） |
| `stem` | 题干（含公式，已剥离选项） |
| `stem_plain` | 剥掉 LaTeX/markdown 标记的纯文本，用于全文检索 |
| `options` | 选择题选项数组；非选择题为空 |
| `answer` | 答案，**可直接渲染**（数学答案已补 `$$` 定界符） |
| `answer_plain` | 答案纯文本，用于判题比对 |
| `solution` | 解析 / 解题过程全文 |
| `formulas` | 该题涉及的所有公式（去重、保序），便于按知识点检索 |
| `images` | 该题内的图片引用路径 |
| `fingerprint` | 由题干+选项算出的 12 位指纹，**入题库查重直接用这个** |
| `confidence` | `high` / `medium` / `low`，见下 |
| `flags` | 疑点标记，见下 |
| `raw` | 该题在原文里的原始行（含题号），便于回溯定位 |

### 置信度与 flags

切题**宁可少切也要标出来**，绝不猜。带疑点的题一律进 `flags`：

| flag | 含义 | 处理建议 |
|---|---|---|
| `answer_inferred` | 答案是从解析里推断的（取最后一个公式或整块短文本），不是文档明写的 | 抽查 |
| `answer_not_extracted` | 有解析但没能提取出答案字段 | 人工填 |
| `no_answer_block` | 文档有答案区，但这题没有对应答案 | 人工填 |
| `inline_split` | 该题来自「一行黏了多道题」的行内二次切分（PDF/OCR 常见） | **必查** |
| `empty_stem` | 只有题号没有题干 | 必查 |
| `possible_step_list` | 同一大节内同号反复出现 ≥3 次，疑似把「解题步骤」当成了题目 | 建议关掉切题或人工删 |

`confidence` 由 flags 推导：有 `inline_split` 降为 `medium`，有 `empty_stem` /
`possible_step_list` 降为 `low`，其余 `high`。

### 识别的题号写法

```
第 1 题 / 第1题： / 第 1 题（稍进阶）      第N题
1.  xxx / 1、xxx / 1）xxx                N.
（1）xxx / (1) xxx                       (N)
习题 3 / 例题 2                          题组标号
```

**大节**（`一、提公因式法（3 题）`）会被识别为 `section` 而不是题目，
所以「每节里题号都从 1 重新开始」不会造成错乱。

### 答案区怎么对上号

中文讲义绝大多数是「前半部分题目、后半部分参考答案」，切完必须回填答案：

1. 遇到 `参考答案` / `答案解析` / `解答` 这类独立成行的标题 → 进入答案模式；
2. 有些讲义不写这四个字，直接重新从第 1 题列答案 —— 这种情况靠**题号回退**识别
   （同一大节内题号开始变小即视为答案区）；
3. 答案区里的 `第 N 题` / `N.` 按序号挂回对应题目。

### 已知边界

- **一道题里的小问**（(1)(2)(3)）目前会留在题干里，不拆成独立题目 —— 拆成独立
  题目会破坏原题的完整性。
- **PDF 路线的题目合并**（相邻两题被识别成一行）靠行内二次切分兜底，但 OCR 把
  `x^2-1` 和 `2.解方程` 这类黏连还原得对不对，取决于文本里是否还留着题干动词
  （「解方程」「因式分解」等）。切出来的题一律带 `inline_split`，请抽查。
- 切题是**规则驱动**的，不做语义理解。排版极其特殊的讲义（题号藏在表格里、
  用图标代替题号）需要先人工整理。

### 回归测试

```bash
python tests/test_splitter.py
```

35 项断言，覆盖题号识别、大节/题目区分、选项拆解（同行与分行）、答案回填、
OCR 黏连切分、解题步骤防误切、分节重编号防误判。改完切题逻辑先跑它。

---

## 输出结构

```
<文件名>_out/
├── <文件名>.md          # Markdown：公式为 $...$ / $$...$$，图片引用 assets/xxx
├── <文件名>.html        # 独立 HTML，公式由 MathJax 渲染，可直接打印
├── assets/              # 抽取出的图片
├── questions.json       # 结构化题目（仅 --split 时生成，见「切题」一节）
└── questions.csv        # 同内容表格版
```

---

## 文件结构

```
doc2md/
├── run.py                       命令行入口
├── tests/
│   ├── sample_mixed.md          多题型回归样本
│   └── test_splitter.py         切题器回归测试（35 项断言，直接 python 跑）
└── doc2md/
    ├── config.py                配置（模型源、目录约定）
    ├── postprocess.py           Markdown 清洗：公式定界符统一、噪声清理
    ├── splitter.py              切题：Markdown → 结构化题目（题干/选项/答案/解析）
    ├── render.py                Markdown → HTML（MathJax）
    └── parsers/
        ├── docx_parser.py       docx → Markdown（pandoc）
        └── pdf_parser.py        pdf  → Markdown（PP-StructureV3）
```

---

## 已知限制

- **扫描件/拍照件**：PP-StructureV3 自带文本检测，可用，但准确率取决于清晰度。
- **复杂排版公式**（多行对齐、矩阵、大括号分段函数）：识别可能丢结构，输出后需要抽查。
- **相邻短行会被合并**：PDF 路线是「版面块 → 文本」的粒度，两行挨得近的题目
  （如 `1. 因式分解：x^2-1` / `2. 解方程：2x+3=7`）可能被识别成一行。这是
  PP-StructureV3 的块级行为，不是本项目引入的；题目分得开的讲义不受影响。
- **Word 里的图片显示尺寸会丢**：pandoc 对带尺寸的图输出裸 `<img style="width:6.1in">`，
  本项目统一转成标准 markdown 图片语法以保证可移植，尺寸交给渲染端自适应。
- **公式定界符**统一为 `$...$` / `$$...$$`。若下游题库系统用 `\(...\)`，在
  `postprocess.clean()` 里改一行即可。
- HTML 默认引用 CDN 上的 MathJax，**离线环境需把 mathjax.js 下载到本地**并在
  `config.MATHJAX_URL` 改成相对路径。
- **速度**：PDF 路线在 CPU 上很慢 —— 模型加载约 40~60 秒（每次进程启动都要付），
  之后单页推理：`fast` 档约 40 秒，`default` 档公式密集页可超过 11 分钟。
  `--no-tables` / `--no-formula` 能明显提速。批量处理务必在同一个进程里循环。
- **公式识别的上限**：`default` 档（PP-FormulaNet_plus-L）能出干净 LaTeX，但 CPU 上
  太慢；`fast` 档（S 版模型）会把单词逐字空格化（`\operatorname{S u b l a y e r}`）。
  也就是说 **CPU 上「快」和「公式准」目前无法兼得**，涉及公式的讲义请优先要 Word 原件。
- **不做 OCR 方向的兜底**：扫描件能跑，但准确率取决于清晰度，且公式识别同样受上面限制。
- **切题是规则驱动的**：题号藏在表格里、用图标代替题号、或一道题跨页断裂的讲义，
  需要先人工整理。详见上面「切题 → 已知边界」。
