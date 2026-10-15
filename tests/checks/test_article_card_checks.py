"""照編輯卡寫的文章多的程式檢查（ADR-0025 §3.7、ADR-0026 §3.2、PRD §7、#110）：只用程式判斷，不看寫得好不好。

- front matter 有 `title`、`description`、`genre`、`theme`，文體與主題各一個、是合法的 slug、跟編輯卡一樣。
- 參考來源每一筆有網址，網址是這張單真的有的（原文、分析抓過的來源、作者素材裡的）；從報告開始時原文在第一條。
- 角色資料列出的句型（`voice.md` 的 `sentence_patterns`）照它寫的次數擋；規則內容在角色資料，不在程式裡。
- 圖文分鏡的每一張圖（概念圖、數據圖）在文章裡都有圖位，圖位只有這幾張。
- 從自己的想法開始寫的沒有原文：不查原文出處與「不提原文」；文體是 essay 從報告開始時可以回應原文。

內容全部是為測試寫的虛構短句。
"""

import re

import pytest

from evidence_forge.checks import (
    ArticleRules,
    CheckEntry,
    OverlapThresholds,
    SentencePattern,
    article_checks,
    check_figure_slots,
    check_front_matter,
    check_reference_urls,
    check_sentence_patterns,
    parse_sentence_patterns,
)

pytestmark = pytest.mark.promise("FR-RW-01")

FILE = "stages/rewrite/1/article.md"
URL = "https://seed.example/posts/night-library"
SOURCE = "https://b.example/2"
TITLE = "夜間圖書館的帳"
GENRES = frozenset({"essay", "research-review", "explainer"})
THEMES = frozenset({"tools", "education"})
FRONT = '---\ntitle: 圖書館開到半夜\ndescription: 延長開放的成本在人\ngenre: research-review\ntheme: education\n---\n\n'
BODY = "# 圖書館開到半夜\n\n延長開放的成本不在電費，在人。\n\n"
REFERENCES = f"## 參考來源\n\n- 王小明，〈{TITLE}〉，{URL}\n- 〈加班的成本〉，{SOURCE}\n"
ARTICLE = FRONT + BODY + REFERENCES
NOT_A_BUT_B = SentencePattern(name="「不是 A，而是 B」", pattern=re.compile(r"不是[^。！？\n]{1,40}而是"), max=1)


def _failed(entries: list[CheckEntry]) -> list[str]:
    return [e.reason for e in entries if e.status == "fail"]


def _front(article: str, **rules: object) -> list[CheckEntry]:
    options: dict[str, object] = {"genres": GENRES, "themes": THEMES}
    options.update(rules)
    return check_front_matter(article, file=FILE, **options)  # type: ignore[arg-type]


def test_front_matter_齊全_文體主題合法而且跟編輯卡一樣_就過() -> None:
    assert _failed(_front(ARTICLE, genre="research-review", theme="education")) == []


@pytest.mark.parametrize(
    "front, needle",
    [
        ("", "front matter"),
        ("---\ntitle: 標題\ngenre: research-review\ntheme: education\n---\n\n", "description"),
        ("---\ntitle: 標題\ndescription: 說明\ntheme: education\n---\n\n", "genre"),
        ("---\ntitle: 標題\ndescription: 說明\ngenre: opinion\ntheme: education\n---\n\n", "opinion"),
        ("---\ntitle: 標題\ndescription: 說明\ngenre: research-review\ntheme: ai\n---\n\n", "ai"),
        ("---\ntitle: 標題\ndescription: 說明\ngenre: [essay, explainer]\ntheme: education\n---\n\n", "genre"),
        ("---\ntitle: 標題\ndescription: 說明\ngenre: essay\ngenre: explainer\ntheme: education\n---\n\n", "genre"),
        ("---\ntitle: 標題\ndescription: 說明\ngenre: research-review\ntheme: tools\ntheme: education\n---\n\n", "theme"),
        ("---\ntitle: [壞掉\n---\n\n", "front matter"),
    ],
)
def test_front_matter_缺欄位_值不在清單_出現兩次_都擋(front: str, needle: str) -> None:
    failed = _failed(_front(front + BODY + REFERENCES))
    assert failed and any(needle in reason for reason in failed), failed


def test_front_matter_的文體與主題要跟編輯卡定的一樣() -> None:
    failed = _failed(_front(ARTICLE, genre="explainer", theme="tools"))
    assert len(failed) == 2 and "explainer" in failed[0] and "tools" in failed[1]


def _urls(article: str, *, original: bool = True, known: frozenset[str] | None = None) -> list[str]:
    return _failed(
        check_reference_urls(
            article,
            file=FILE,
            original_url=URL if original else None,
            original_title=TITLE if original else None,
            known_urls=frozenset({URL, SOURCE}) if known is None else known,
        )
    )


def test_參考來源每一筆有網址_而且是這張單有的網址() -> None:
    assert _urls(ARTICLE) == []


def test_參考來源有一筆沒有網址_擋() -> None:
    article = ARTICLE + "- 〈沒有網址的書〉\n"
    assert any("網址" in reason for reason in _urls(article))


def test_參考來源的網址不是這張單有的_擋_不能自己編來源() -> None:
    article = ARTICLE + "- 〈編出來的〉，https://invented.example/x\n"
    assert any("invented.example" in reason for reason in _urls(article))


def test_沒有參考來源一節_擋() -> None:
    assert any("參考來源" in reason for reason in _urls(FRONT + BODY))


def test_從自己的想法寫的_沒有用到外面的來源_參考來源可以只寫一句說明() -> None:
    article = FRONT + BODY + "## 參考來源\n\n本文只根據作者自己的經驗與筆記。\n"
    assert _urls(article, original=False, known=frozenset()) == []


def test_原文是貼上的文字沒有網址_原文那一筆不用網址() -> None:
    article = FRONT + BODY + f"## 參考來源\n\n- 〈{TITLE}〉（創作者貼上的文字）\n- 〈加班的成本〉，{SOURCE}\n"
    entries = check_reference_urls(
        article, file=FILE, original_url=None, original_title=TITLE, known_urls=frozenset({SOURCE})
    )
    assert _failed(entries) == []


def test_句型照角色資料寫的次數擋_次數以內就過() -> None:
    once = FRONT + "# 標題\n\n成本不是電費，而是人。\n\n" + REFERENCES
    twice = once.replace("而是人。", "而是人。重點不是預算，而是排班。")

    assert _failed(check_sentence_patterns(once, file=FILE, patterns=(NOT_A_BUT_B,))) == []
    failed = _failed(check_sentence_patterns(twice, file=FILE, patterns=(NOT_A_BUT_B,)))
    assert failed and "「不是 A，而是 B」" in failed[0] and "2" in failed[0]


def test_句型只算正文_參考來源裡的不算() -> None:
    article = FRONT + "# 標題\n\n成本不是電費，而是人。\n\n## 參考來源\n\n- 〈不是書，而是館〉，" + SOURCE + "\n"
    assert _failed(check_sentence_patterns(article, file=FILE, patterns=(NOT_A_BUT_B,))) == []


def test_句型規則從角色資料讀_格式不對就說是哪一條() -> None:
    meta = {"sentence_patterns": [{"name": "甲", "pattern": "不是.+而是", "max": 1}]}
    [found] = parse_sentence_patterns(meta)
    assert (found.name, found.max) == ("甲", 1)
    assert parse_sentence_patterns({}) == ()
    for bad in (
        {"sentence_patterns": "不是一份清單"},
        {"sentence_patterns": [{"name": "甲", "pattern": "(", "max": 1}]},
        {"sentence_patterns": [{"name": "甲", "pattern": "x", "max": -1}]},
        {"sentence_patterns": [{"pattern": "x", "max": 1}]},
        {"sentence_patterns": [{"name": "甲", "pattern": "x" * 300, "max": 1}]},
    ):
        with pytest.raises(ValueError, match="sentence_patterns"):
            parse_sentence_patterns(bad)


def _figures(article: str, slots: tuple[int, ...] | None) -> list[str]:
    return _failed(check_figure_slots(article, file=FILE, slots=slots))


def test_分鏡的每張圖都有圖位_附圖說_就過() -> None:
    article = FRONT + BODY + "![白天與晚上的使用者](./images/fig-1.webp)\n\n文字。\n\n![九個月的人流](./images/fig-2.webp)\n\n" + REFERENCES
    assert _figures(article, (1, 2)) == []


def test_少一張圖位_擋_說是哪一張() -> None:
    article = FRONT + BODY + "![白天與晚上的使用者](./images/fig-1.webp)\n\n" + REFERENCES
    assert any("fig-2" in reason for reason in _figures(article, (1, 2)))


def test_圖位沒有圖說_或多出分鏡沒排的圖_都擋() -> None:
    no_alt = FRONT + BODY + "![](./images/fig-1.webp)\n\n" + REFERENCES
    extra = FRONT + BODY + "![圖](./images/fig-1.webp)\n\n![多的](./images/fig-2.webp)\n\n" + REFERENCES
    assert _figures(no_alt, (1,))
    assert any("fig-2" in reason for reason in _figures(extra, (1,)))


def test_分鏡不是結構化的_圖位不查() -> None:
    assert _figures(ARTICLE, None) == []


def _all(article: str, **changes: object) -> list[CheckEntry]:
    arguments: dict[str, object] = {
        "article": article,
        "article_file": FILE,
        "output": {"title": "圖書館開到半夜"},
        "output_file": "stages/rewrite/1/article.json",
        "schema": {"type": "object"},
        "original_url": URL,
        "original_title": TITLE,
        "original_author": None,
        "texts": {"S2": "完全不同的來源。"},
        "thresholds": OverlapThresholds(ngram_size=5, max_ngram_overlap=0.1, max_common_tokens=12),
        "rules": ArticleRules(
            genres=GENRES, themes=THEMES, genre="research-review", theme="education",
            figures=(), patterns=(NOT_A_BUT_B,), known_urls=frozenset({URL, SOURCE}),
        ),
    }
    arguments.update(changes)
    return article_checks(**arguments)  # type: ignore[arg-type]


def test_照編輯卡寫的文章_全部檢查都過() -> None:
    assert _failed(_all(ARTICLE)) == []


def test_從自己的想法寫的沒有原文_不查原文出處與不提原文() -> None:
    article = FRONT + "# 標題\n\n原文這兩個字在這裡沒有指誰。\n\n## 參考來源\n\n- 〈加班的成本〉，" + SOURCE + "\n"
    entries = _all(article, original_url=None, original_title=None)
    assert _failed(entries) == []


def test_隨筆從報告開始_可以回應原文_原文照樣列在第一條() -> None:
    article = FRONT.replace("research-review", "essay") + f"# 標題\n\n讀了〈{TITLE}〉，原作者說的我同意一半。\n\n" + REFERENCES
    rules = ArticleRules(
        genres=GENRES, themes=THEMES, genre="essay", theme="education", known_urls=frozenset({URL, SOURCE}),
        answers_original=True,
    )
    assert _failed(_all(article, rules=rules)) == []
    swapped = FRONT.replace("research-review", "essay") + "# 標題\n\n內文。\n\n" + (
        f"## 參考來源\n\n- 〈加班的成本〉，{SOURCE}\n- 王小明，〈{TITLE}〉，{URL}\n"
    )
    assert any("第一條" in reason for reason in _failed(_all(swapped, rules=rules)))


def test_不是隨筆_從報告開始_內文提原文照擋() -> None:
    article = FRONT + f"# 標題\n\n讀了〈{TITLE}〉，原作者說的我同意一半。\n\n" + REFERENCES
    assert _failed(_all(article))
