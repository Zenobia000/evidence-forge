"""圖文節奏檢查 L1～L6：門檻是正文字數（中文一字算一、英文一個詞算一）。"""

from pathlib import Path

from evidence_forge.figures.layout import (
    CLUSTER_MIN,
    FIRST_MAX,
    GAP_MAX,
    check_file,
    check_text,
    count,
)


def text(n: int) -> str:
    """剛好 n 個字的一段文字。"""
    return "字" * n


def figure(name: str = "a") -> str:
    return f"![{name} 的替代文字](./images/{name}.webp)\n*{name} 的圖說*"


def article(*parts: str) -> str:
    return "\n\n".join(parts) + "\n"


def rules(md: str) -> list[str]:
    return [p.rule for p in check_text(md).problems]


GOOD = article(
    "# 標題",
    text(300),
    figure("a"),
    text(400),
    "## 小節",
    text(300),
    figure("b"),
    text(300),
)


def test_節奏正常的文章全部通過() -> None:
    report = check_text(GOOD)

    assert report.ok
    assert report.stats.figures == 2
    assert report.stats.first_figure_at == 300
    assert report.stats.chars == 1300


def test_字數的算法() -> None:
    assert count("中文字") == 3
    assert count("two English words") == 3
    assert count("[連結文字](https://example.com/a-long-url)") == 4  # 網址不算
    assert count("版本 2.0 和 50%") == 5  # 三個中文字加兩個數字詞


def test_L1_第一張圖在_1000_字內() -> None:
    ok = article(text(FIRST_MAX), figure(), text(300))
    late = article(text(FIRST_MAX + 1), figure(), text(300))

    assert rules(ok) == []
    problems = check_text(late).problems
    assert [p.rule for p in problems] == ["L1"]
    assert str(FIRST_MAX + 1) in problems[0].message


def test_L2_兩個圖或表之間不超過_1300_字() -> None:
    ok = article(text(300), figure("a"), text(GAP_MAX), figure("b"), text(300))
    long_gap = article(text(300), figure("a"), text(GAP_MAX + 1), figure("b"), text(300))

    assert rules(ok) == []
    assert rules(long_gap) == ["L2"]


def test_L2_表格也算一次視覺停頓() -> None:
    table = "| a | b |\n|---|---|\n| 1 | 2 |"
    md = article(text(300), figure("a"), text(1000), table, text(1000), figure("b"), text(300))

    report = check_text(md)

    assert report.ok
    assert report.stats.tables == 1


def test_L2_沒有圖的長文章也會被擋() -> None:
    md = article(text(GAP_MAX + 1))
    report = check_text(md)

    assert [p.rule for p in report.problems] == ["L2"]
    assert report.stats.figures == 0
    assert report.stats.first_figure_at is None
    assert report.stats.longest_gap == GAP_MAX + 1


def test_L3_兩張圖之間至少_250_字() -> None:
    ok = article(text(300), figure("a"), text(CLUSTER_MIN), figure("b"), text(300))
    close = article(text(300), figure("a"), text(CLUSTER_MIN - 1), figure("b"), text(300))

    assert rules(ok) == []
    problems = check_text(close).problems
    assert [p.rule for p in problems] == ["L3"]
    assert str(CLUSTER_MIN - 1) in problems[0].message


def test_L4_圖不能直接放在小標下() -> None:
    md = article(text(300), "## 小節", figure(), text(300))

    assert rules(md) == ["L4"]


def test_L4_文章第一個區塊就是圖也算() -> None:
    assert rules(article(figure(), text(300))) == ["L4"]


def test_L5_圖不能當一節的結尾() -> None:
    mid = article(text(300), figure(), "## 下一節", text(300))
    last = article(text(300), figure())

    assert rules(mid) == ["L5"]
    assert rules(last) == ["L5"]


def test_L6_沒有替代文字() -> None:
    md = article(text(300), "![](./images/a.webp)\n*圖說*", text(300))

    assert rules(md) == ["L6"]


def test_L6_沒有圖說() -> None:
    md = article(text(300), "![替代文字](./images/a.webp)", text(300))

    problems = check_text(md).problems
    assert [p.rule for p in problems] == ["L6"]
    assert "caption" in problems[0].message


def test_front_matter_與參考資料不算正文() -> None:
    md = "---\ntitle: x\ncover: ./images/cover.webp\n---\n\n" + article(
        text(300), figure(), text(300), "## 參考資料", text(3000)
    )

    report = check_text(md)

    assert report.ok
    assert report.stats.chars == 600


def test_行號從_front_matter_算起() -> None:
    md = "---\ntitle: x\n---\n" + article(text(FIRST_MAX + 1), figure(), text(300))

    (problem,) = check_text(md).problems

    lines = md.splitlines()
    assert lines[problem.line - 1].startswith("![")  # L1 指在第一張圖的那一行


def test_allow_只豁免指定規則的指定行() -> None:
    md = article(text(300), figure("a"), text(100), figure("b"), text(300))
    (problem,) = check_text(md).problems
    assert problem.rule == "L3"

    waived = check_text(md, allow={problem.key})
    wrong_line = check_text(md, allow={f"L3@{problem.line + 1}"})
    wrong_rule = check_text(md, allow={f"L1@{problem.line}"})

    assert waived.ok
    assert [p.rule for p in wrong_line.problems] == ["L3"]
    assert [p.rule for p in wrong_rule.problems] == ["L3"]


def test_一篇文章可以同時違反多條規則() -> None:
    md = article(text(1200), "## 小節", figure("a"), figure("b"))

    assert sorted(rules(md)) == ["L1", "L3", "L4", "L5"]


def test_check_file_讀檔(tmp_path: Path) -> None:
    path = tmp_path / "article.md"
    path.write_text(GOOD, encoding="utf-8")

    assert check_file(path).ok


# ---- 圖片行與圖說依「行」的結構認，不靠空行 ----


def test_圖片行緊貼在文字後面不空行也算一張圖() -> None:
    # 1001 字後面直接接圖片行（沒有空行）：以前整段被當成文字，圖數是 0、L1 漏報
    md = text(FIRST_MAX + 1) + "\n![圖](x.webp)\n*圖說*\n\n" + text(300) + "\n"

    report = check_text(md)

    assert report.stats.figures == 1
    assert [p.rule for p in report.problems] == ["L1"]
    assert report.stats.first_figure_at == FIRST_MAX + 1


def test_圖片行緊貼在文字前面不空行文字照算() -> None:
    md = "![圖](x.webp)\n*圖說*\n" + text(300) + "\n"

    report = check_text(md)

    assert report.stats.figures == 1
    assert report.stats.chars == 300
    assert [p.rule for p in report.problems] == ["L4"]  # 文章第一個區塊就是圖


def test_圖片與圖說之間隔一個空行不算沒有圖說() -> None:
    md = article(text(300), "![替代文字](x.webp)\n\n*圖說*", text(300))

    assert check_text(md).ok


def test_圖片與圖說之間隔了文字就不是它的圖說() -> None:
    md = article(text(300), "![替代文字](x.webp)", text(50), "*斜體的一句話*", text(300))

    problems = check_text(md).problems

    assert [p.rule for p in problems] == ["L6"]
    assert "caption" in problems[0].message


def test_圖片行的行號就是那一行() -> None:
    md = "intro line\n![](x.webp)\n*圖說*\n\n" + text(300) + "\n"  # 沒有替代文字的圖在第 2 行

    (problem,) = check_text(md).problems

    assert (problem.rule, problem.line) == ("L6", 2)


def test_標題後面緊接文字行不被吃掉() -> None:
    md = "# 標題\n" + text(300) + "\n![圖](x.webp)\n*圖說*\n\n" + text(300) + "\n"

    report = check_text(md)

    assert report.ok
    assert report.stats.chars == 600


# ---- 程式碼區塊裡的東西不是圖、不是標題 ----


def test_程式碼區塊裡的圖片語法不算一張圖() -> None:
    code = "```md\n![fake](x.webp)\n*假的圖說*\n```"
    md = article(text(300), code, text(300))

    report = check_text(md)

    assert report.stats.figures == 0
    assert report.ok


def test_波浪線圍欄和更長的圍欄也一樣() -> None:
    tilde = "~~~\n![fake](x.webp)\n~~~"
    longer = "````\n```\n![fake](y.webp)\n```\n````"  # 外層四個反引號，裡面的三個不算收尾
    md = article(text(300), tilde, text(300), longer, text(300))

    assert check_text(md).stats.figures == 0


def test_圍欄裡的空行與井字號不會切開區塊也不當標題() -> None:
    code = "```python\n# 這是註解\n\n![fake](x.webp)\n\n## 參考資料\nprint('x')\n```"
    md = article(text(300), code, text(300), figure("real"), text(300))

    report = check_text(md)

    assert report.stats.figures == 1  # 只有後面那張真的圖
    assert report.ok
    # 圍欄後面的文章沒有被「## 參考資料」截斷：圍欄外的 300 + 300 + 300 都算到
    assert report.stats.chars >= 900


def test_圍欄內的文字照算字數_和原腳本一致() -> None:
    code = "```\n" + text(200) + "\n```"
    md = article(text(300), code, text(300))

    assert check_text(md).stats.chars == 800


def test_沒有收尾的圍欄一路算到文章結尾() -> None:
    md = article(text(300), "```\n![fake](x.webp)", text(10))

    report = check_text(md)

    assert report.stats.figures == 0


def test_圍欄後面的真圖行號照常() -> None:
    md = "```\ncode\n```\n\n![](real.webp)\n*圖說*\n\n" + text(300) + "\n"  # 沒有替代文字的圖在第 5 行

    (problem,) = check_text(md).problems

    assert (problem.rule, problem.line) == ("L6", 5)
