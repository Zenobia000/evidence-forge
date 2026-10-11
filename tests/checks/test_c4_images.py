"""C4 最小版：稿子裡每張圖的網址都要在工單的 `assets.json` 裡（PRD §7、ADR-0016）。

S0 的 `assets.json` 是空的，所以 S0 的稿子嵌任何一張圖都不會過。
"""

import json
from pathlib import Path

import pytest

from evidence_forge.checks import Location, check_images, load_asset_urls

pytestmark = pytest.mark.promise("FR-VF-01")

FILE = "drafts/draft.v1.md"


def test_draft_without_images_passes_even_with_no_assets() -> None:
    entries = check_images(
        "# 標題\n\n一段[連結](https://example.com)。\n", set(), file=FILE
    )
    assert [entry.status for entry in entries] == ["pass"]
    assert entries[0].check == "C4"


def test_any_embedded_image_fails_when_assets_are_empty() -> None:
    draft = "# 標題\n\n開頭配圖 ![原文的圖](https://cdn.example.com/a.png)\n"
    entries = check_images(draft, set(), file=FILE)
    assert [entry.status for entry in entries] == ["fail"]
    assert entries[0].location == Location(file=FILE, line=3, column=6)
    assert entries[0].excerpt == "https://cdn.example.com/a.png"


def test_image_listed_in_assets_passes() -> None:
    draft = '![圖](<https://cdn.example.com/a.png> "標題")\n'
    entries = check_images(draft, {"https://cdn.example.com/a.png"}, file=FILE)
    assert [entry.status for entry in entries] == ["pass"]


@pytest.mark.parametrize(
    ("draft", "url"),
    [
        ('![圖](https://x.example/a.png "說明")', "https://x.example/a.png"),
        (
            "![圖][hero]\n\n[hero]: https://x.example/hero.jpg",
            "https://x.example/hero.jpg",
        ),
        (
            '<p><img alt="x" src="https://x.example/b.gif"></p>',
            "https://x.example/b.gif",
        ),
        ("<IMG SRC='https://x.example/c.webp'/>", "https://x.example/c.webp"),
    ],
)
def test_every_image_syntax_is_caught(draft: str, url: str) -> None:
    entries = check_images(draft, set(), file=FILE)
    assert [(entry.status, entry.excerpt) for entry in entries] == [("fail", url)]


def test_each_unlisted_image_is_listed_separately() -> None:
    draft = "![a](https://x.example/1.png)\n![b](https://x.example/2.png)\n"
    entries = check_images(draft, {"https://x.example/1.png"}, file=FILE)
    assert [(entry.status, entry.excerpt) for entry in entries] == [
        ("fail", "https://x.example/2.png")
    ]


def test_images_inside_code_are_not_embedded() -> None:
    draft = (
        "語法是 `![alt](url)`。\n\n```markdown\n![a](https://x.example/1.png)\n```\n"
    )
    entries = check_images(draft, set(), file=FILE)
    assert [entry.status for entry in entries] == ["pass"]


def test_missing_assets_file_means_no_assets(tmp_path: Path) -> None:
    assert load_asset_urls(tmp_path / "assets" / "assets.json") == frozenset()


def test_assets_file_lists_urls(tmp_path: Path) -> None:
    path = tmp_path / "assets.json"
    path.write_text(
        json.dumps([{"id": "a1", "url": "https://x.example/1.png", "license": "ok"}]),
        encoding="utf-8",
    )
    assert load_asset_urls(path) == frozenset({"https://x.example/1.png"})


def test_empty_assets_file_means_no_assets(tmp_path: Path) -> None:
    path = tmp_path / "assets.json"
    path.write_text("[]", encoding="utf-8")
    assert load_asset_urls(path) == frozenset()


def test_malformed_assets_file_is_an_error_not_an_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "assets.json"
    path.write_text('{"assets": "oops"}', encoding="utf-8")
    with pytest.raises(ValueError, match=r"assets\.json"):
        load_asset_urls(path)


@pytest.mark.parametrize(
    ("draft", "url"),
    [
        # 說明文字裡有成對的中括號
        ("![圖 [1] 示意](http://x.example/a.png)", "http://x.example/a.png"),
        # 說明文字換行
        ("![第一行\n第二行](http://x.example/b.png)", "http://x.example/b.png"),
        # 網址裡有成對的括號
        ("![](https://e.example/Foo_(bar).png)", "https://e.example/Foo_(bar).png"),
        # data-src 不是 src，真正顯示的是 src
        (
            '<img data-src="https://ok.example/1.png" src="https://x.example/c.png">',
            "https://x.example/c.png",
        ),
        # 參照定義重複時，第一個生效（CommonMark）
        (
            "![圖][x]\n\n[x]: https://x.example/first.png\n[x]: https://ok.example/1.png",
            "https://x.example/first.png",
        ),
        # 一行行內程式碼裡有三個反引號，不是程式碼區塊的開頭，後面的圖照樣算
        ("```js``` 是寫法\n\n![](https://x.example/d.png)", "https://x.example/d.png"),
    ],
)
def test_tricky_image_syntax_is_still_caught(draft: str, url: str) -> None:
    entries = check_images(draft, {"https://ok.example/1.png"}, file=FILE)
    assert [(entry.status, entry.excerpt) for entry in entries] == [("fail", url)]


@pytest.mark.parametrize(
    ("draft", "url"),
    [
        # 反斜線跳脫的反引號不是行內程式碼的開頭，後面的圖會顯示出來
        ("前文 \\` ![x](https://x.example/e.png) ` 後文", "https://x.example/e.png"),
        # 只有 srcset、沒有 src 的 <img>，瀏覽器照樣顯示
        ('<img srcset="https://x.example/f.png 1x">', "https://x.example/f.png"),
        # 參照定義寫在引言區塊裡也算數（CommonMark）
        ("![x][r]\n\n> [r]: https://x.example/g.png", "https://x.example/g.png"),
        # 說明文字裡有兩層中括號
        ("![a [b [c]] d](https://x.example/h.png)", "https://x.example/h.png"),
        # <image> 會被瀏覽器當成 <img>
        ('<image src="https://x.example/i.png">', "https://x.example/i.png"),
        # SVG 裡的 <image href>
        (
            '<svg><image href="https://x.example/j.png"/></svg>',
            "https://x.example/j.png",
        ),
    ],
)
def test_image_forms_found_in_review_are_caught(draft: str, url: str) -> None:
    entries = check_images(draft, {"https://ok.example/1.png"}, file=FILE)
    assert url in [entry.excerpt for entry in entries if entry.status == "fail"]


def test_every_candidate_in_a_srcset_must_be_listed() -> None:
    draft = '<img src="https://ok.example/1.png" srcset="https://ok.example/1.png 1x, https://x.example/2x.png 2x">'
    entries = check_images(draft, {"https://ok.example/1.png"}, file=FILE)
    assert [(entry.status, entry.excerpt) for entry in entries] == [
        ("fail", "https://x.example/2x.png")
    ]
