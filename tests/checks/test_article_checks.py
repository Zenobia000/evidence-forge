"""重構段的程式檢查（PRD v3.0 §7、ADR-0020 §3.4、ADR-0024、`rewrite.feature`）：

- 擋：文章是標準 Markdown；結尾的「參考來源」一定列原文出處（有網址看網址，沒有看標題）；
  標題不用文類骨架的字；參考來源以外的內文不提原文標題與原作者。
- 只顯示、不擋：文章正文跟原文、跟每一份來源的逐字重疊率，各一條 C3，只會 pass 或 warn。
"""

import pytest

from evidence_forge.checks import (
    CheckEntry,
    OverlapThresholds,
    article_checks,
    check_original_not_in_body,
    check_references,
    check_skeleton_headings,
)

pytestmark = pytest.mark.promise("FR-RW-01")

FILE = "stages/rewrite/1/article.md"
URL = "https://seed.example/posts/night-library"
TITLE = "夜間圖書館的帳"
THRESHOLDS = OverlapThresholds(
    ngram_size=5, max_ngram_overlap=0.1, max_common_tokens=12
)
SCHEMA = {"type": "object", "required": ["title"]}

BODY = "# 圖書館開到半夜，值得嗎\n\n我認為延長開放的成本不在電費，在人。\n\n"
REFERENCES = (
    "## 參考來源\n\n"
    f"- 王小明，〈{TITLE}〉，{URL}\n"
    "- 〈加班的成本〉，https://b.example/2\n"
)


def _check(article: str, **changes: object) -> list[CheckEntry]:
    arguments: dict[str, object] = {
        "article": article,
        "article_file": FILE,
        "output": {"title": "圖書館開到半夜，值得嗎"},
        "output_file": "stages/rewrite/1/article.json",
        "schema": SCHEMA,
        "original_url": URL,
        "original_title": TITLE,
        "original_author": None,
        "texts": {"原文": "完全不同的原文。", "S2": "完全不同的來源。"},
        "thresholds": THRESHOLDS,
    }
    arguments.update(changes)
    return article_checks(**arguments)  # type: ignore[arg-type]


def _references(
    article: str, url: str | None = URL, author: str | None = None
) -> list[CheckEntry]:
    return check_references(
        article,
        file=FILE,
        original_url=url,
        original_title=TITLE,
        original_author=author,
    )


def _failed(entries: list[CheckEntry]) -> list[CheckEntry]:
    return [entry for entry in entries if entry.status == "fail"]


def test_結尾參考來源列了原文網址_全部通過() -> None:
    entries = _check(BODY + REFERENCES)
    assert _failed(entries) == []
    assert {entry.check for entry in entries} == {"C3", "C5"}


def test_參考來源沒列原文網址_擋下並說要列原文出處() -> None:
    article = BODY + "## 參考來源\n\n- 〈加班的成本〉，https://b.example/2\n"
    [failure] = _failed(_references(article))
    assert "原文" in failure.reason and URL in failure.reason


def test_沒有參考來源一節_擋下() -> None:
    [failure] = _failed(_references(BODY))
    assert "參考來源" in failure.reason


def test_參考來源不在結尾_後面還有別節_擋下() -> None:
    article = BODY + REFERENCES + "\n## 後記\n\n還有一段。\n"
    [failure] = _failed(_references(article))
    assert "結尾" in failure.reason


def test_原文網址只出現在正文_參考來源沒列_照樣擋() -> None:
    article = (
        f"# 標題\n\n原文在 {URL}。\n\n## 參考來源\n\n- 別的，https://b.example/2\n"
    )
    assert _failed(_references(article))


def test_網址結尾多一個斜線也認得() -> None:
    article = BODY + f"## 參考來源\n\n- {TITLE}，{URL}/\n"
    assert not _failed(_references(article))


def test_貼上的原文沒有網址_參考來源列標題就算() -> None:
    article = BODY + f"## 參考來源\n\n- 〈{TITLE}〉（創作者貼上的原文）\n"
    assert not _failed(_references(article, url=None))
    missing = BODY + "## 參考來源\n\n- 〈加班的成本〉\n"
    assert _failed(_references(missing, url=None))


@pytest.mark.promise("FR-RW-01", "NFR-RUN-03")
def test_照抄原文一大段_重疊率只提醒不擋_每份來源各一條() -> None:
    copied = "延長開放的成本不在電費而在人力排班與夜間安全"
    article = f"# 標題\n\n{copied}，這是我的看法。\n\n" + REFERENCES
    entries = _check(
        article, texts={"原文": f"作者說{copied}。", "S2": "完全不同的來源。"}
    )

    assert _failed(entries) == []
    overlap = [entry for entry in entries if entry.check == "C3"]
    summaries = [entry for entry in overlap if entry.location is None]
    assert [entry.reason.split("：", 1)[0] for entry in summaries] == ["原文", "S2"]
    assert [entry.status for entry in summaries] == ["warn", "pass"]
    assert any(entry.excerpt == copied for entry in overlap)


def test_參考來源裡照列的標題與網址不算重疊() -> None:
    # 參考來源本來就照抄標題與網址；算進去的話，標題一長就被提醒
    long_title = "城市夜間閱讀空間的排班與讀者行為十年觀察紀錄"
    article = BODY + f"## 參考來源\n\n- 〈{long_title}〉，{URL}\n"
    entries = _check(
        article, original_title=long_title, texts={"原文": long_title + "。正文。"}
    )
    assert {entry.status for entry in entries if entry.check == "C3"} == {"pass"}


def test_文章不是標準_Markdown_擋下() -> None:
    article = "<html><body>文章</body></html>\n\n" + REFERENCES
    assert any(entry.check == "C5" for entry in _failed(_check(article)))


def test_article_json_不合_schema_擋下() -> None:
    assert _failed(_check(BODY + REFERENCES, output={"headline": "打錯欄位"}))


def test_網址寫法不同但是同一個網址_也認得() -> None:
    # 主機大小寫、結尾的 # 片段不算差別（跟分析報告的來源清單檢查用同一套比對）
    article = BODY + f"## 參考來源\n\n- {TITLE}，https://SEED.example/posts/night-library#top\n"
    assert not _failed(_references(article))


# --- 同一筆參考條目要有標題、網址，素材有作者時也要有作者（rewrite.feature、PRD §6 第 4 點，#92 審查）


def test_網址只是前綴相同的另一篇_不算列了原文() -> None:
    article = BODY + f"## 參考來源\n\n- 〈{TITLE}〉，{URL}-2\n"
    [failure] = _failed(_references(article))
    assert URL in failure.reason


def test_同一筆有網址但沒有原文標題_擋下() -> None:
    article = BODY + f"## 參考來源\n\n- 原文，{URL}\n"
    [failure] = _failed(_references(article))
    assert "標題" in failure.reason


def test_標題與網址分在兩筆_不算() -> None:
    article = BODY + f"## 參考來源\n\n- 〈{TITLE}〉\n- {URL}\n"
    assert _failed(_references(article))


def test_素材有作者時_同一筆也要有作者() -> None:
    without = BODY + f"## 參考來源\n\n- 〈{TITLE}〉，{URL}\n"
    [failure] = _failed(_references(without, author="王小明"))
    assert "作者" in failure.reason and "王小明" in failure.reason
    assert not _failed(_references(BODY + REFERENCES, author="王小明"))


def test_一筆可以跨行_縮排的續行算同一筆() -> None:
    article = BODY + f"## 參考來源\n\n- 王小明，〈{TITLE}〉\n  {URL}\n"
    assert not _failed(_references(article, author="王小明"))


def test_網頁標題後面的站名可以省略() -> None:
    # 素材項目的標題常是網頁的 <title>：「文章標題 | 站名」；照抄文章標題、站名另寫也算
    article = BODY + f"## 參考來源\n\n- {TITLE}，王小明，某某部落格，{URL}\n"
    found = check_references(
        article,
        file=FILE,
        original_url=URL,
        original_title=f"{TITLE} | 某某部落格",
        original_author="王小明",
    )
    assert not _failed(found)


def test_條目只列網址_標題剛好是網址的一部分_不算列了標題() -> None:
    # 標題與作者要在網址以外的文字裡找：網址本身含著標題不算（#92 第二輪審查）
    url = "https://example.com/night-library"
    article = BODY + f"## 參考來源\n\n- {url}\n"
    found = check_references(
        article,
        file=FILE,
        original_url=url,
        original_title="night-library",
        original_author="example",
    )
    [failure] = _failed(found)
    assert "標題" in failure.reason and "作者" in failure.reason


# --- ADR-0024：骨架不當標題、內文不提原文與原作者 -------------------------------------


@pytest.mark.parametrize(
    "heading",
    [
        "## 先講結論",
        "## 結論",
        "## 結論先講",
        "## 結論與行動",
        "## 論據一：進度要放到磁碟上",
        "## 論據 2",
        "### 反方怎麼說，我怎麼回應",
        "## 反方說法與回應",
        "## 我的建議",
        "## 建議",
        "## 2. **反方**",
        "## 反方觀點",
    ],
)
def test_標題用了骨架字_擋下並點名那個標題(heading: str) -> None:
    article = f"# 圖書館開到半夜\n\n開頭。\n\n{heading}\n\n內容。\n\n" + REFERENCES
    [failure] = _failed(check_skeleton_headings(article, file=FILE))
    text = heading.lstrip("#").strip()
    assert failure.reason.startswith(f"標題「{text}」")
    assert "骨架" in failure.reason
    assert failure.location is not None and failure.location.line == 5


@pytest.mark.parametrize(
    "heading",
    [
        "## 晚上十點，誰還在圖書館",
        "## 建議先別急著砍購書預算",
        "## 錢從哪裡來",
        "## 結算一下人力成本",
    ],
)
def test_標題寫這一節實際在講什麼_通過(heading: str) -> None:
    article = f"# 圖書館開到半夜\n\n{heading}\n\n內容。\n\n" + REFERENCES
    assert _failed(check_skeleton_headings(article, file=FILE)) == []


def test_程式碼區塊裡的骨架字標題不算() -> None:
    article = "# 標題\n\n```\n## 結論\n```\n\n" + REFERENCES
    assert _failed(check_skeleton_headings(article, file=FILE)) == []


def test_內文沒提原文標題與作者_通過() -> None:
    entries = check_original_not_in_body(
        BODY + REFERENCES, file=FILE, original_title=TITLE, original_author="王小明"
    )
    assert _failed(entries) == []


def test_內文提到原文標題_擋下() -> None:
    article = f"# 圖書館開到半夜\n\n這篇〈{TITLE}〉談的是延長開放。\n\n" + REFERENCES
    [failure] = _failed(
        check_original_not_in_body(article, file=FILE, original_title=TITLE, original_author=None)
    )
    assert TITLE in failure.reason and "參考來源" in failure.reason
    assert failure.location is not None and failure.location.line == 3


def test_內文提到原作者_擋下() -> None:
    article = "# 圖書館開到半夜\n\n王 小明認為延長開放是對的。\n\n" + REFERENCES
    [failure] = _failed(
        check_original_not_in_body(
            article, file=FILE, original_title=TITLE, original_author="王小明"
        )
    )
    assert "王小明" in failure.reason


def test_拉丁字母的作者名要整個字對上() -> None:
    article = "# 標題\n\nSamsung 的手機與 Sam Lee 無關。\n\n" + REFERENCES
    [failure] = _failed(
        check_original_not_in_body(article, file=FILE, original_title=TITLE, original_author="Sam Lee")
    )
    assert "Sam Lee" in failure.reason
    only_brand = "# 標題\n\nSamsung 的手機。\n\n" + REFERENCES
    assert _failed(
        check_original_not_in_body(only_brand, file=FILE, original_title=TITLE, original_author="Sam")
    ) == []


def test_網頁標題的站名不算原文標題_文章標題那一段才算() -> None:
    article = "# 圖書館開到半夜\n\n某某部落格上常有人討論這件事。\n\n" + REFERENCES
    assert _failed(
        check_original_not_in_body(
            article, file=FILE, original_title=f"{TITLE} | 某某部落格", original_author=None
        )
    ) == []
    mentioned = f"# 圖書館開到半夜\n\n{TITLE}這個說法。\n\n" + REFERENCES
    assert _failed(
        check_original_not_in_body(
            mentioned, file=FILE, original_title=f"{TITLE} | 某某部落格", original_author=None
        )
    )


def test_原文標題太短_不比對_以免誤擋() -> None:
    article = "# 圖書館開到半夜\n\n夜間閱讀這件事。\n\n" + REFERENCES
    assert _failed(
        check_original_not_in_body(article, file=FILE, original_title="夜間閱讀", original_author=None)
    ) == []


def test_文章的檢查合起來_骨架字標題與提到原作者都擋() -> None:
    article = "# 圖書館開到半夜\n\n王小明說得對。\n\n## 我的建議\n\n內容。\n\n" + REFERENCES
    reasons = [entry.reason for entry in _failed(_check(article, original_author="王小明"))]
    assert any("我的建議" in reason for reason in reasons)
    assert any("王小明" in reason for reason in reasons)
