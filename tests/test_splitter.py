# -*- coding: utf-8 -*-
"""切题器回归测试。

不依赖 pytest，直接运行：

    python tests/test_splitter.py

覆盖的坑：
  * 三种题号写法（第 N 题 / N. / （N））
  * 大节 vs 题目的区分（「一、选择题」不该被当成题目）
  * 答案区按题号回填
  * 选项拆解（同一行多选项 / 分行选项）
  * OCR 把多题黏成一行时的行内二次切分
  * 解题步骤不误切（「**步骤1：xxx**」不是题目）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from doc2md import splitter      # noqa: E402

_failures = []
_passed = 0


def check(name, cond, extra=""):
    global _passed
    if cond:
        _passed += 1
        print("  [ok] %s" % name)
    else:
        _failures.append(name)
        print("  [FAIL] %s %s" % (name, extra))


def test_mixed():
    print("\n[1] 多题型综合样本（选择题/填空题/解答题 + 答案区）")
    text = (ROOT / "tests" / "sample_mixed.md").read_text(encoding="utf-8")
    doc = splitter.split(text, source="sample_mixed.md")

    check("题数 = 6", doc["count"] == 6, "实际 %d" % doc["count"])
    check("大节 = 3", len(doc["sections"]) == 3, str(doc["sections"]))
    check("大节标题正确",
          doc["sections"] and doc["sections"][0].startswith("一、选择题"),
          str(doc["sections"]))

    q = {x["number"]: x for x in doc["questions"]}

    check("题1 归属大节「一、选择题」",
          q[1]["section"].startswith("一、选择题"), repr(q[1]["section"]))
    check("题1 选项 4 个（同行）", q[1]["options"] == ["1", "2", "3", "4"],
          str(q[1]["options"]))
    check("题2 选项 4 个（分行）", q[2]["options"] == ["5", "6", "-6", "-5"],
          str(q[2]["options"]))
    check("题1 题干已剥离选项",
          "A." not in q[1]["stem"] and "最小值" in q[1]["stem"], repr(q[1]["stem"]))
    check("题3 无选项", q[3]["options"] == [], str(q[3]["options"]))
    check("题3 题干含公式", "mx + 9" in q[3]["stem_plain"], repr(q[3]["stem_plain"]))
    check("题5 归属大节「三、解答题」",
          q[5]["section"].startswith("三、解答题"), repr(q[5]["section"]))

    check("题1 答案回填 = A", q[1]["answer"].strip() == "A", repr(q[1]["answer"]))
    check("题2 答案回填 = B", q[2]["answer"].strip() == "B", repr(q[2]["answer"]))
    check("题3 答案回填含 pm 6", "pm 6" in q[3]["answer"], repr(q[3]["answer"]))
    check("题3 答案可渲染（带定界符）",
          q[3]["answer"].lstrip().startswith("$"), repr(q[3]["answer"]))
    check("题5 答案解析含 x_{1} = 5",
          "x_{1} = 5" in q[5]["solution"], repr(q[5]["solution"][:80]))
    check("题6 有解析（非公式答案）",
          "配方" in q[6]["solution"] and q[6]["answer"] == "",
          repr(q[6]["solution"][:60]) + " / ans=" + repr(q[6]["answer"]))
    check("题6 标了 answer_not_extracted",
          "answer_not_extracted" in q[6]["flags"], str(q[6]["flags"]))

    check("全部题干非空", all(x["stem"] for x in doc["questions"]))
    check("公式去重", len(q[1]["formulas"]) == len(set(q[1]["formulas"])),
          str(q[1]["formulas"]))
    check("指纹唯一", len({x["fingerprint"] for x in doc["questions"]}) == doc["count"])
    check("统计一致", doc["stats"]["count"] == doc["count"])


def test_inline_glue():
    print("\n[2] OCR 多题黏成一行（行内二次切分）")
    text = "# 课后补充\n\n1 .因式分解：x^2-12.解方程：2x+3=7\n"
    doc = splitter.split(text, source="pdf_sim")
    check("切成 2 题", doc["count"] == 2, "实际 %d" % doc["count"])
    if doc["count"] == 2:
        a, b = doc["questions"]
        check("题1 题干 = 因式分解：x^2-1", a["stem_plain"] == "因式分解：x^2-1",
              repr(a["stem_plain"]))
        check("题2 题干 = 解方程：2x+3=7", b["stem_plain"] == "解方程：2x+3=7",
              repr(b["stem_plain"]))
        check("题1 题号 = 1 / 题2 题号 = 2", (a["number"], b["number"]) == (1, 2),
              "%s %s" % (a["number"], b["number"]))
        check("都标了 inline_split",
              "inline_split" in a["flags"] and "inline_split" in b["flags"])
        check("置信度降级为 medium",
              a["confidence"] == "medium" and b["confidence"] == "medium")


def test_step_list():
    print("\n[3] 解题步骤不应被误切为题目")
    text = ("**配方法标准流程**\n\n"
            "**步骤1：移常数项**\n\n将常数项移到等号右侧：\n\n"
            "$$\nx^{2} + bx = - c\n$$\n\n"
            "**步骤2：配方**\n\n两边同加一次项系数一半的平方。\n")
    doc = splitter.split(text, source="steps")
    check("0 道题", doc["count"] == 0, "实际 %d" % doc["count"])


def test_section_restart():
    print("\n[4] 分节重编号不应被当成答案区")
    text = ("### 一、提公因式法\n\n1.  $x^{2} - 3x = 0$\n\n2.  $2x^{2} + 4x = 0$\n\n"
            "### 二、十字相乘法\n\n1.  $x^{2} + 5x + 6 = 0$\n\n2.  $x^{2} - 5x + 6 = 0$\n")
    doc = splitter.split(text, source="sec")
    check("4 道题（不是 2 题 + 2 条答案）", doc["count"] == 4,
          "实际 %d" % doc["count"])
    check("带答案数为 0", doc["stats"]["with_answer"] == 0,
          str(doc["stats"]))
    check("全部 high 置信度", doc["stats"]["high"] == 4, str(doc["stats"]))
    check("题3 归属第二节",
          doc["questions"][2]["section"].startswith("二、"), 
          repr(doc["questions"][2]["section"]))


def test_answer_restart():
    print("\n[5] 无「参考答案」字样时，靠题号回退识别答案区")
    text = ("第 1 题\n\n求 $1+1$。\n\n第 2 题\n\n求 $2+2$。\n\n"
            "第 1 题\n\n$$2$$\n\n第 2 题\n\n$$4$$\n")
    doc = splitter.split(text, source="restart")
    check("2 道题", doc["count"] == 2, "实际 %d" % doc["count"])
    if doc["count"] == 2:
        check("题1 答案回填 = 2", "2" in doc["questions"][0]["answer"],
              repr(doc["questions"][0]["answer"]))
        check("题2 答案回填 = 4", "4" in doc["questions"][1]["answer"],
              repr(doc["questions"][1]["answer"]))


def test_pandoc_paper():
    print("\n[6] pandoc 转义题号 1\\. 与（N）小问的形态竞争（组卷网类试卷）")
    text = (
        "### 一、选择题\n\n"
        "1\\. 求 $1+1$ 的值（　　）\n\n"
        "A. 1 B. 2 C. 3 D. 4\n\n"
        "2\\. 求 ![img](assets/image4.png) 的绝对值（　　）\n\n"
        "A. ![a](assets/image5.png) B. 2 C. 3 D. 4\n\n"
        "### 二、解答题\n\n"
        "3\\. 如图，完成下列小问\n\n"
        "（1）画出图形\n\n"
        "（2）说明理由\n"
    )
    doc = splitter.split(text, source="pandoc")
    check("3 道题（（1）（2）是小问，不单独成题）", doc["count"] == 3,
          "实际 %d" % doc["count"])
    q = {x["number"]: x for x in doc["questions"]}
    check("转义题号 1\\. 2\\. 3\\. 全部识别", set(q) == {1, 2, 3}, str(set(q)))
    check("题1 选项 4 个", q[1]["options"] == ["1", "2", "3", "4"],
          str(q[1]["options"]))
    check("题2 图片选项不干扰递增链",
          q[2]["options"] == ["![a](assets/image5.png)", "2", "3", "4"],
          str(q[2]["options"]))
    check("题3 题干保留小问（1）（2）",
          "（1）" in q[3]["stem"] and "（2）" in q[3]["stem"],
          repr(q[3]["stem"][:80]))


def test_img_regex_spaces():
    print("\n[7] markdown 图片路径含空格（Windows 绝对路径）也能改写")
    from doc2md.parsers import docx_parser
    m = docx_parser._MD_IMG.search(r"![](F:\Program Files\x\out/media/image4.png)")
    check("含空格路径能匹配", m is not None)
    if m:
        check("src 完整取到右括号", m.group(2).endswith("image4.png"),
              repr(m.group(2)))
        rel = docx_parser._to_assets(m.group(2))
        check("映射成 assets/image4.png", rel == "assets/image4.png", repr(rel))


def main():
    for fn in (test_mixed, test_inline_glue, test_step_list,
               test_section_restart, test_answer_restart,
               test_pandoc_paper, test_img_regex_spaces):
        fn()
    print("\n" + "=" * 60)
    if _failures:
        print("失败 %d 项 / 通过 %d 项" % (len(_failures), _passed))
        for f in _failures:
            print("  - " + f)
        return 1
    print("全部通过（%d 项断言）" % _passed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
