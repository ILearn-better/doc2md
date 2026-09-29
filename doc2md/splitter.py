# -*- coding: utf-8 -*-
"""按题号把 Markdown 讲义切分成结构化题目，供入题库使用。

设计原则
--------
1. **保守优先**：宁可少切并把疑点标出来，也不能把「解题步骤」误当成题目。
   每一步判定都能解释，低置信度的题带 `flags` 交人工兜底。
2. **答案回填**：中文讲义绝大多数是「前半部分题目、后半部分参考答案」，
   所以切完必须按题号把答案区的块挂回对应题目，否则入库只有题干没答案。
3. **公式安全**：行间公式 `$$...$$` 与代码块内部绝不参与题号识别，
   否则矩阵里的数字会被当成题号。
4. **只处理 Markdown**：解析器已经把公式转成了 LaTeX，在这一层做规则切分最直观，
   不需要引入额外的中间数据结构。

支持的题号写法（实测覆盖常见讲义）
----------------------------------
    第 1 题 / 第1题： / 第 1 题（稍进阶）      —— Word 讲义最常见
    1.  xxx / 1、xxx / 1）xxx                 —— markdown 有序列表 / 裸题号
    （1）xxx / (1) xxx                        —— 括号题号
    习题 3 / 例题 2                           —— 题组标号
    一、提公因式法（3 题）                     —— 识别为大节（section），不是题目
    参考答案 / 答案解析 / 解答                 —— 切换到答案区

输出结构见 `split()` 的 docstring。
"""
import csv
import hashlib
import json
import re
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# 行级扫描：区分「正文 / 行间公式 / 代码块」
# 行间公式与代码块内部一律不允许切题。
# ---------------------------------------------------------------------------
KIND_TEXT = "text"
KIND_MATH = "math"
KIND_CODE = "code"


def scan_lines(text):
    """返回 [(原始行, 类型)]，类型见 KIND_*。"""
    out = []
    in_math = False
    in_code = False
    for raw in text.split("\n"):
        s = raw.strip()
        if in_code:
            out.append((raw, KIND_CODE))
            if s.startswith("```"):
                in_code = False
            continue
        if s.startswith("```"):
            in_code = True
            out.append((raw, KIND_CODE))
            continue
        if in_math:
            out.append((raw, KIND_MATH))
            if "$$" in s:
                in_math = False
            continue
        if s.startswith("$$"):
            out.append((raw, KIND_MATH))
            # 单独一行 $$ 开头 → 进入多行公式；$$...$$ 单行则不算
            if not (s.endswith("$$") and len(s) > 4):
                in_math = True
            continue
        out.append((raw, KIND_TEXT))
    return out


# ---------------------------------------------------------------------------
# 行首清洗：去掉 markdown 井号前缀与整行加粗包裹
# ---------------------------------------------------------------------------
_LEAD_HASH = re.compile(r"^[ \t]*(?:#{1,6}[ \t]*)+")
_BOLD_WRAP = re.compile(r"^(?:\*\*|__)(.+?)(?:\*\*|__)$")
_BLOCKQUOTE = re.compile(r"^[ \t]*>[ \t]?")


def strip_head(line):
    """-> (用于匹配的裸文本, 去空白后的原行)"""
    s = line.strip()
    s = _BLOCKQUOTE.sub("", s)
    s = _LEAD_HASH.sub("", s)
    m = _BOLD_WRAP.match(s)
    if m:
        s = m.group(1).strip()
    return s, line.strip()


# ---------------------------------------------------------------------------
# 题号 / 答案区 / 大节 的识别规则
# ---------------------------------------------------------------------------
_R_ZHANG = re.compile(
    r"^(?:第\s*(\d{1,4})\s*[题问]|习题\s*(\d{1,3})|例题\s*(\d{1,3}))\s*[:：、.]?\s*(.*)$")
_R_NUM = re.compile(r"^(\d{1,3})(?!\d)\s*[.、)）]\s*(?!\d)(.*)$")
_R_PAREN = re.compile(r"^[（(]\s*(\d{1,3})\s*[)）]\s*(.*)$")

_ANSWER_HEAD = re.compile(
    r"^(?:参考|标准)?\s*(?:答案|解答|解析|答案与解析|答案解析|参考答案与解析)\s*$")

_SECTION_HASH = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_SECTION_ZH = re.compile(r"^[一二三四五六七八九十]{1,3}\s*[、.．]\s*\S")
_SECTION_MAX_LEN = 48

# 行内二次切分：OCR 常把多道题黏成一行，如
#   「1 .因式分解：x^2-12.解方程：2x+3=7」
# 数字后面紧跟题干动词时，即使前面没有空白也切开。
_INLINE_TRIGGER = re.compile(
    r"(?<=[\s\)\]\}。，,;；a-zA-Z0-9])"
    r"(\d{1,2})\s*[.、]\s*"
    r"(?=(?:解方程|解不等式|解方程组|因式分解|计算|化简|求值|求|证明|作图|画出|判断|"
    r"选择|填空|已知|设|若|用|如图|解答|配方|分解因式))")

# 选项标记：A. / A、 / （A） / A)
_OPT_PREFIX = re.compile(r"[（(]?([A-Ha-h])\s*[)）.、．]\s*(?=\S)")
_OPT_TAIL_LIMIT = 120     # 末项超过这个长度说明后面混进了别的内容

# 答案显式标记
_ANS_MARK = re.compile(r"^[ \t]*(?:参考)?答案\s*[:：]\s*(.*)$", re.M)
# 「整块就是答案」的长度上限（选择题「A」、填空题「$\pm 6$」这类）
_SHORT_ANSWER_LIMIT = 80
# 出现这些词说明整块是解题过程/提示，而不是光秃秃的答案
_SOLUTION_HINT = re.compile(
    r"(提示|解析|证明|理由|说明|因为|所以|故|则|由|可得|代入|两边|配方|化简|"
    r"整理|依题意|解\s*[:：])")
_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.S)
_INLINE_MATH = re.compile(r"\$([^$\n]+?)\$")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def split(text, source=""):
    """把 Markdown 讲义切成结构化题目。

    :param text: 解析后（已 clean / promote）的 markdown 文本
    :param source: 来源文件名，写进结果便于溯源
    :return: dict，结构如下::

        {
          "source": "...",
          "count": 5,
          "sections": ["一、提公因式法（3 题）", ...],
          "questions": [
            {
              "index": 1,                 # 全局序号（1 起）
              "number": 1,                # 文档里的原题号
              "number_kind": "第N题",      # 题号写法类型
              "section": "一、提公因式法（3 题）",
              "section_index": 0,
              "stem": "用配方法解方程：\\n\\n$$...$$",
              "stem_plain": "用配方法解方程： x^{2} - 2x - 3 = 0",
              "options": ["1", "2"],
              "answer": "x = 3 或 x = -1",
              "solution": "$$...$$\\n\\n$$...$$",
              "formulas": ["x^{2} - 2x - 3 = 0", ...],
              "images": [],
              "fingerprint": "a1b2c3d4e5f6",
              "confidence": "high",
              "flags": [],
              "raw": "### 第 1 题\\n\\n用配方法解方程：..."
            }
          ]
        }
    """
    rows = scan_lines(text)

    questions = []      # 题干区
    answers = []        # 答案区
    sections = []
    cur_section = None

    cur = None          # 正在累积的块
    mode = "stem"       # stem | answer
    last_numbers = []   # 题干区已见题号
    last_q_section = None

    def close():
        """收尾当前块。"""
        nonlocal cur
        if cur is None:
            return
        payload = "\n".join(cur["lines"]).strip("\n")
        item = {
            "number": cur["number"],
            "number_kind": cur["kind"],
            "section": cur["section"],
            "text": payload,
            "raw": cur["raw"],
        }
        (questions if cur["phase"] == "stem" else answers).append(item)
        cur = None

    for raw, kind in rows:
        # 公式 / 代码块：只可能属于当前块，永不作为切点
        if kind in (KIND_MATH, KIND_CODE):
            if cur is not None:
                cur["lines"].append(raw)
            continue

        bare, stripped = strip_head(raw)
        if not stripped:
            if cur is not None:
                cur["lines"].append("")
            continue

        # ① 答案区标志
        if _ANSWER_HEAD.match(bare):
            close()
            mode = "answer"
            continue

        # ② 题号
        q = _match_question(bare)
        if q is not None:
            number, rest, qkind = q
            # 题干区里题号回退、且没有换大节 → 说明进入了答案区（兜底，
            # 有些讲义不写「参考答案」四个字，直接重新从第 1 题开始列答案）
            if (mode == "stem" and last_numbers
                    and number <= last_numbers[-1]
                    and cur_section == last_q_section):
                close()
                mode = "answer"
            close()
            cur = {
                "number": number,
                "kind": qkind,
                "section": cur_section,
                "lines": [rest] if rest else [],
                "phase": mode,
                "raw": raw.strip(),
            }
            if mode == "stem":
                last_numbers.append(number)
                last_q_section = cur_section
            continue

        # ③ 大节标题（未命中题号的其他标题行）
        if mode == "stem" and _is_section(raw, bare):
            close()
            sections.append(bare)
            cur_section = bare
            continue

        # ④ 正文
        if cur is not None:
            cur["lines"].append(raw)

    close()

    # 行内二次切分（OCR 黏连补偿）
    questions = _explode_inline(questions, sections)

    # 答案回填
    result = _build(questions, answers, sections, source)
    return result


def _match_question(bare):
    """-> (number, rest, kind) | None"""
    m = _R_ZHANG.match(bare)
    if m:
        num = m.group(1) or m.group(2) or m.group(3)
        return int(num), (m.group(4) or "").strip(), "第N题"
    m = _R_NUM.match(bare)
    if m:
        return int(m.group(1)), m.group(2).strip(), "N."
    m = _R_PAREN.match(bare)
    if m:
        return int(m.group(1)), m.group(2).strip(), "(N)"
    return None


def _is_section(raw, bare):
    if _SECTION_HASH.match(raw):
        return True
    # 中文序号打头且不长 → 视为大节（如「一、提公因式法（3 题）」）
    if len(bare) <= _SECTION_MAX_LEN and _SECTION_ZH.match(bare):
        return True
    return False


def _explode_inline(questions, sections):
    """把「一行里黏了多道题」的情况拆开。

    只在行首题号已识别、剩余正文里又出现「数字+题干动词」时才拆，
    避免误伤普通正文。拆出来的题带 inline_split 标记。
    """
    out = []
    for q in questions:
        segs = _split_inline(q["text"])
        if len(segs) <= 1:
            out.append(q)
            continue
        for i, (num, body) in enumerate(segs):
            number = q["number"] if num is None else num
            item = dict(q)
            item["number"] = number
            item["text"] = body
            item["inline"] = True
            if i == 0 and num is None:
                item["number_kind"] = q["number_kind"]
            else:
                item["number_kind"] = "N."
            out.append(item)
    return out


def _split_inline(seg):
    """-> [(number|None, text)]；None 表示延续前文。"""
    cuts = list(_INLINE_TRIGGER.finditer(seg))
    if not cuts:
        return [(None, seg)]
    out = []
    head = seg[:cuts[0].start()].strip()
    if head:
        out.append((None, head))
    for i, m in enumerate(cuts):
        end = cuts[i + 1].start() if i + 1 < len(cuts) else len(seg)
        body = seg[m.end():end].strip()
        out.append((int(m.group(1)), body))
    return out if len(out) > 1 else [(None, seg)]


# ---------------------------------------------------------------------------
# 题干内部结构化：选项 / 公式 / 答案
# ---------------------------------------------------------------------------
def extract_options(text):
    """拆出选择题选项。返回 (options, 去掉选项后的题干)。

    只有当出现 ≥2 个「字母递增」的标记时才认定为选项，
    避免把正文里的单个「A.」误切成选项。
    """
    marks = list(_OPT_PREFIX.finditer(text))
    if len(marks) < 2:
        return [], text

    letters = [m.group(1).upper() for m in marks]
    best = None
    for i in range(len(letters)):
        j = i + 1
        while j < len(letters) and ord(letters[j]) == ord(letters[j - 1]) + 1:
            j += 1
        if j - i >= 2 and (best is None or (j - i) > (best[1] - best[0])):
            best = (i, j)
    if best is None:
        return [], text

    i, j = best
    options = []
    for k in range(i, j):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(text)
        seg = text[marks[k].end():end].strip()
        if k == j - 1 and len(seg) > _OPT_TAIL_LIMIT:
            seg = seg[:_OPT_TAIL_LIMIT].rstrip() + "…"
        options.append(seg)

    # 题干 = 首个选项标记之前的内容（选项之后的尾巴通常是「答案：B」之类，
    # 不塞回题干，避免污染题目正文）
    stem = text[:marks[i].start()].strip()
    return options, stem or text


def find_formulas(text):
    """抽出该题里所有公式（不含定界符），便于题库做检索与查重。"""
    raw = [m.group(1).strip() for m in _DISPLAY_MATH.finditer(text)]
    for line in text.split("\n"):
        if line.strip().startswith("$$"):
            continue
        raw.extend(m.group(1).strip() for m in _INLINE_MATH.finditer(line))
    # 去重但保持出现顺序（题干与解析之间常有重复，入库时不需要冗余）
    seen = set()
    out = []
    for f in raw:
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def find_images(text):
    return re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)


def plain(text):
    """剥掉 markdown / LaTeX 标记，得到便于检索与查重的纯文本。"""
    t = _DISPLAY_MATH.sub(lambda m: " " + m.group(1) + " ", text)
    t = _INLINE_MATH.sub(lambda m: " " + m.group(1) + " ", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\\(?:left|right|text|frac|sqrt|pm|neq|geq|leq|times|cdot|"
               r"operatorname|ldots|cdots|quad|qquad|overline)\b", " ", t)
    t = re.sub(r"[*_`>#|]", "", t)
    t = re.sub(r"\\[a-zA-Z]+", " ", t)
    t = re.sub(r"[{}\\]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _infer_answer(solution):
    """从解析文本里推断答案。-> (答案文本, 是否需要补公式定界符, 是否显式标注)

    三档，从可靠到兜底：
      1. 显式写了「答案：xxx」→ 直接用 xxx
      2. 有行间公式 → 取最后一个。数学题的最终结果通常写在最后一步
      3. 整块很短且不含解题用语 → 整块就是答案（选择题「A」、填空题「$\\pm 6$」）
    """
    m = _ANS_MARK.search(solution)
    if m and m.group(1).strip():
        return m.group(1).strip(), False, True

    blocks = _DISPLAY_MATH.findall(solution)
    if blocks:
        return blocks[-1].strip(), True, False

    body = solution.strip()
    if body and len(body) <= _SHORT_ANSWER_LIMIT and not _SOLUTION_HINT.search(body):
        return body, False, False
    return "", False, False


# ---------------------------------------------------------------------------
# 组装最终结果
# ---------------------------------------------------------------------------
def _build(questions, answers, sections, source):
    section_index = {s: i for i, s in enumerate(sections)}

    ans_by_number = {}
    for a in answers:
        # 同号答案取第一个（后面重复的多半是续页）
        ans_by_number.setdefault(a["number"], a["text"])

    out_questions = []
    for qi, q in enumerate(questions, 1):
        stem_full = q["text"]
        options, stem = extract_options(stem_full)
        raw_stem = stem.strip()

        solution = ans_by_number.get(q["number"], "")
        if solution:
            answer, wrap, explicit = _infer_answer(solution)
        else:
            answer, wrap, explicit = "", False, False
        if answer and wrap:
            # 推断来的答案本身是公式块内容，补上定界符让下游可直接渲染
            answer = "$$\n%s\n$$" % answer

        flags = []
        if not raw_stem:
            flags.append("empty_stem")
        if q.get("inline"):
            flags.append("inline_split")
        if answers and not solution:
            flags.append("no_answer_block")
        if solution and not answer:
            flags.append("answer_not_extracted")
        if answer and not explicit:
            flags.append("answer_inferred")

        if raw_stem:
            confidence = "high"
        else:
            confidence = "low"
        if "inline_split" in flags and confidence == "high":
            confidence = "medium"

        plain_stem = plain(raw_stem)
        fp = hashlib.sha1((plain_stem + "|" + plain(" ".join(options))).encode("utf-8"))
        out_questions.append({
            "index": qi,
            "number": q["number"],
            "number_kind": q["number_kind"],
            "section": q["section"] or "",
            "section_index": section_index.get(q["section"], -1),
            "stem": raw_stem,
            "stem_plain": plain_stem,
            "options": options,
            "answer": answer,
            "answer_plain": plain(answer),
            "solution": solution.strip(),
            "formulas": find_formulas(stem_full + "\n" + solution),
            "images": find_images(stem_full),
            "fingerprint": fp.hexdigest()[:12],
            "confidence": confidence,
            "flags": flags,
            "raw": q["raw"],
        })

    # 题号疑似「步骤列表」而非题目：**同一大节内**同号反复出现 3 次以上。
    # 注意必须按 (section, number) 计数 —— 讲义常见「一、方法A」下 1 2 3、
    # 「二、方法B」下又是 1 2 3，这是正常的分节重编号，不是步骤列表。
    counts = {}
    for q in out_questions:
        key = (q["section"], q["number"])
        counts[key] = counts.get(key, 0) + 1
    if out_questions and max(counts.values()) >= 3:
        suspect = {k for k, v in counts.items() if v >= 3}
        for q in out_questions:
            if (q["section"], q["number"]) in suspect:
                q["flags"].append("possible_step_list")
                q["confidence"] = "low"

    stats = {
        "count": len(out_questions),
        "high": sum(1 for q in out_questions if q["confidence"] == "high"),
        "medium": sum(1 for q in out_questions if q["confidence"] == "medium"),
        "low": sum(1 for q in out_questions if q["confidence"] == "low"),
        "with_answer": sum(1 for q in out_questions if q["answer"] or q["solution"]),
        "sections": len(sections),
    }
    return {
        "source": source,
        "generator": "doc2md.splitter",
        "count": len(out_questions),
        "sections": sections,
        "stats": stats,
        "questions": out_questions,
    }


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
CSV_COLUMNS = ["index", "number", "section", "confidence", "stem_plain", "stem",
               "options", "answer", "answer_plain", "solution", "formulas",
               "fingerprint", "flags", "source"]


def write_outputs(doc, out_dir, files=False):
    """写出 questions.json（主）/ questions.csv。返回 (json_path, csv_path)。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "questions.json"
    json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    csv_path = out_dir / "questions.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for q in doc["questions"]:
            row = {
                "index": q["index"],
                "number": q["number"],
                "section": q["section"],
                "confidence": q["confidence"],
                "stem_plain": q["stem_plain"],
                "stem": q["stem"],
                "options": " | ".join(q["options"]),
                "answer": q["answer"],
                "answer_plain": q["answer_plain"],
                "solution": q["solution"],
                "formulas": " | ".join(q["formulas"]),
                "fingerprint": q["fingerprint"],
                "flags": ",".join(q["flags"]),
                "source": doc["source"],
            }
            writer.writerow(row)

    if files:
        qdir = out_dir / "questions"
        qdir.mkdir(parents=True, exist_ok=True)
        for q in doc["questions"]:
            body = ["# 第 %d 题" % q["index"], ""]
            if q["section"]:
                body += ["> 所属：%s" % q["section"], ""]
            body += [q["stem"], ""]
            if q["options"]:
                body += ["**选项：**", ""]
                body += ["- %s" % o for o in q["options"]]
                body += [""]
            if q["answer"]:
                if q["answer"].lstrip().startswith("$"):
                    body += ["**答案：**", "", q["answer"], ""]
                else:
                    body += ["**答案：**%s" % q["answer"], ""]
            if q["solution"]:
                body += ["**解析：**", "", q["solution"], ""]
            (qdir / ("q%03d.md" % q["index"])).write_text("\n".join(body),
                                                          encoding="utf-8")
    else:
        # 上一轮可能带了 --split-files，留着的旧文件会变成「过期数据」误导使用者
        stale = out_dir / "questions"
        if stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)

    return json_path, csv_path
