"""封面：三層組合、prompt、16:9 裁切、相鄰文章提醒。生圖一律用假的 renderer，不碰任何模型。"""

import io
from pathlib import Path

import pytest

from evidence_forge.figures.brand import DEFAULT_BRAND, Brand, CoverVocabulary, MediumSpec, PresetSpec
from evidence_forge.figures.cover import (
    RENDER_QUALITY,
    RENDER_SIZE,
    CoverError,
    CoverRenderer,
    FakeCoverRenderer,
    build_prompt,
    crop_16_9,
    is_dark,
    layers_of,
    make_cover,
    resolve_layers,
)
from PIL import Image

VOCAB = DEFAULT_BRAND.cover

FRONT_MATTER = '---\ntitle: "{name}"\ncover: "./images/cover.webp"\n{extra}---\n\n正文\n'


def _article(root: Path, name: str, extra: str = "") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "article.md").write_text(FRONT_MATTER.format(name=name, extra=extra), encoding="utf-8")
    return folder


def _brand_with_presets() -> Brand:
    cover = CoverVocabulary(
        forms={"mural": "A wall-sized mural.", "print": "A pulled print."},
        media={
            "tempera": MediumSpec(forms=("mural",), description="Egg tempera."),
            "linocut": MediumSpec(forms=("print",), description="Linocut."),
        },
        styles={"flat": "Flat colour."},
        presets={"wall": PresetSpec(form="mural", medium="tempera", style="flat", preamble="Wall preamble.")},
        shared_rules="Shared rules.",
        market_preamble="Market preamble.",
    )
    return Brand(cover=cover)


# ---- 三層組合 ----


def test_三層選對就組出_prompt_開頭() -> None:
    layers = resolve_layers(VOCAB, form="painting", medium="oil", style="realism")

    assert (layers.form, layers.medium, layers.style) == ("painting", "oil", "realism")
    assert layers.preamble == f"{VOCAB.forms['painting']} {VOCAB.media['oil'].description} {VOCAB.styles['realism']}"


def test_預設名稱直接帶出三層與固定開頭() -> None:
    brand = _brand_with_presets()

    layers = resolve_layers(brand.cover, preset="wall")

    assert (layers.form, layers.medium, layers.style) == ("mural", "tempera", "flat")
    assert layers.preamble == "Wall preamble."


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"form": "sculpture", "medium": "oil", "style": "realism"}, "媒材 oil 只配 painting，不配 sculpture"),
        ({"form": "nope", "medium": "oil", "style": "realism"}, "沒有這個形式"),
        ({"form": "painting", "medium": "nope", "style": "realism"}, "沒有這個媒材"),
        ({"form": "painting", "medium": "oil", "style": "nope"}, "沒有這個風格"),
        ({"form": "painting", "medium": "oil"}, "同時給 form、medium、style"),
        ({}, "同時給 form、medium、style"),
        ({"preset": "ghost"}, "沒有這個預設"),
    ],
)
def test_三層選錯會擋(kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(CoverError, match=message):
        resolve_layers(VOCAB, **kwargs)


def test_prompt_由開頭_主題_共同限制組成() -> None:
    layers = resolve_layers(VOCAB, form="painting", medium="oil", style="realism")

    prompt = build_prompt(VOCAB, layers, "A single stone on a plain.")

    assert prompt == f"{layers.preamble} Subject: A single stone on a plain. {VOCAB.shared_rules}"


def test_市場觀察封面換開頭與限制_沒寫市場限制就沿用共同限制() -> None:
    brand = _brand_with_presets()
    layers = resolve_layers(brand.cover, preset="wall")

    assert build_prompt(brand.cover, layers, "Shelves.", market=True) == (
        "Market preamble. Subject: Shelves. Shared rules."
    )
    own = brand.cover.model_copy(update={"market_rules": "Market rules."})
    assert build_prompt(own, layers, "Shelves.", market=True).endswith("Subject: Shelves. Market rules.")


# ---- 做一張封面 ----


def test_產生封面_存原圖與_16比9_的_png_webp(tmp_path: Path) -> None:
    article = _article(tmp_path, "01-first")
    renderer = FakeCoverRenderer()

    result = make_cover(
        article, renderer, DEFAULT_BRAND,
        subject="A horizon line.", form="painting", medium="ink-wash", style="minimalism",
    )

    images = article / "images"
    assert {p.name for p in result.files} == {"cover-raw.png", "cover.png", "cover.webp"}
    assert Image.open(images / "cover-raw.png").size == (1536, 1024)  # 原圖不裁
    for name in ("cover.png", "cover.webp"):
        assert Image.open(images / name).size == (1600, 900)
    assert result.warnings == []
    # 交給 renderer 的就是組好的 prompt，尺寸與品質照規格
    assert renderer.calls == [(result.prompt, RENDER_SIZE, RENDER_QUALITY)]
    assert "A horizon line." in result.prompt


def test_產生封面會把三層寫進_front_matter_重畫是覆蓋不是累加(tmp_path: Path) -> None:
    article = _article(tmp_path, "01-first")
    brand = DEFAULT_BRAND

    make_cover(article, FakeCoverRenderer(), brand, subject="s", form="painting", medium="oil", style="realism")
    make_cover(article, FakeCoverRenderer(), brand, subject="s", form="photography", medium="film", style="abstract")

    text = (article / "article.md").read_text(encoding="utf-8")
    assert text.count("cover_form:") == text.count("cover_medium:") == text.count("cover_style:") == 1
    assert 'cover_form: "photography"\ncover_medium: "film"\ncover_style: "abstract"' in text
    assert text.index('cover: "./images/cover.webp"') < text.index("cover_form:")  # 接在 cover: 後面


def test_front_matter_沒有_cover_行就放在結尾前(tmp_path: Path) -> None:
    article = tmp_path / "01-first"
    article.mkdir()
    (article / "article.md").write_text('---\ntitle: "t"\n---\n\n正文\n', encoding="utf-8")

    make_cover(article, FakeCoverRenderer(), DEFAULT_BRAND, subject="s", form="painting", medium="oil", style="realism")

    text = (article / "article.md").read_text(encoding="utf-8")
    assert text.startswith('---\ntitle: "t"\ncover_form: "painting"')
    assert text.endswith('cover_style: "realism"\n---\n\n正文\n')


def test_沒有_front_matter_就提醒沒記錄而不是默默略過(tmp_path: Path) -> None:
    article = tmp_path / "01-first"
    article.mkdir()
    (article / "article.md").write_text("只有正文\n", encoding="utf-8")

    result = make_cover(
        article, FakeCoverRenderer(), DEFAULT_BRAND, subject="s", form="painting", medium="oil", style="realism"
    )

    assert result.warnings == ["article.md has no front matter; cover layers were not recorded"]
    assert (article / "article.md").read_text(encoding="utf-8") == "只有正文\n"


def test_還沒有_article_md_也能先畫封面(tmp_path: Path) -> None:
    article = tmp_path / "01-first"

    result = make_cover(
        article, FakeCoverRenderer(), DEFAULT_BRAND, subject="s", form="painting", medium="oil", style="realism"
    )

    assert (article / "images/cover.webp").exists()
    assert result.warnings == []


def test_三層選錯時不會呼叫_renderer(tmp_path: Path) -> None:
    renderer = FakeCoverRenderer()

    with pytest.raises(CoverError):
        make_cover(
            tmp_path / "01-first", renderer, DEFAULT_BRAND,
            subject="s", form="sculpture", medium="oil", style="realism",
        )

    assert renderer.calls == []  # 不花一次生圖的錢


def test_假_renderer_符合介面() -> None:
    renderer: CoverRenderer = FakeCoverRenderer()

    png = renderer.render("p", size=RENDER_SIZE, quality="low")

    assert Image.open(io.BytesIO(png)).format == "PNG"


# ---- 裁切 ----


@pytest.mark.parametrize("size", [(1536, 1024), (1920, 1000), (1000, 1000), (2400, 900)])
def test_任何來源尺寸都裁成_1600x900(size: tuple[int, int]) -> None:
    raw = FakeCoverRenderer(width=size[0], height=size[1]).render("p", size="x", quality="x")

    assert crop_16_9(raw).size == (1600, 900)


def test_置中裁切只切掉上下多出的部分() -> None:
    image = Image.new("RGB", (160, 120), (255, 255, 255))
    for y in range(120):
        for x in range(160):
            if y < 15:  # 上緣 15 像素是黑的；16:9 的高是 90，上下各切 15
                image.putpixel((x, y), (0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")

    cropped = crop_16_9(buffer.getvalue())

    assert cropped.getpixel((800, 2)) == (255, 255, 255)  # 黑邊整條被切掉


# ---- 相鄰文章的提醒 ----


def _cover(article: Path, dark: bool) -> None:
    make_cover(
        article, FakeCoverRenderer(dark=dark), DEFAULT_BRAND,
        subject="s", form="painting", medium="oil", style="realism",
    )


def test_前後篇形式與媒材都一樣就提醒(tmp_path: Path) -> None:
    first = _article(tmp_path, "01-first")
    second = _article(tmp_path, "02-second")
    _cover(first, dark=False)

    result = make_cover(
        second, FakeCoverRenderer(), DEFAULT_BRAND,
        subject="s", form="painting", medium="oil", style="abstract",  # 風格不同不算
    )

    assert result.warnings == ["neighbour 01-first also uses painting/oil; change the form or the medium"]


def test_換了形式或媒材就不提醒(tmp_path: Path) -> None:
    first = _article(tmp_path, "01-first")
    second = _article(tmp_path, "02-second")
    _cover(first, dark=False)

    same_form = make_cover(
        second, FakeCoverRenderer(), DEFAULT_BRAND,
        subject="s", form="painting", medium="ink-wash", style="realism",
    )

    assert same_form.warnings == []


def test_舊文章只記了預設名稱也認得出形式與媒材(tmp_path: Path) -> None:
    brand = _brand_with_presets()
    old = _article(tmp_path, "01-old", 'cover_medium: "wall"\n')
    new = _article(tmp_path, "02-new")

    assert layers_of(brand.cover, old) == ("mural", "tempera")
    result = make_cover(new, FakeCoverRenderer(), brand, subject="s", preset="wall")

    assert len(result.warnings) == 1 and "01-old" in result.warnings[0]


def test_連續三篇最多一張深色封面(tmp_path: Path) -> None:
    first = _article(tmp_path, "01-first")
    second = _article(tmp_path, "02-second")
    third = _article(tmp_path, "03-third")
    _cover(first, dark=True)
    assert is_dark(first / "images/cover.webp")

    # 第二篇用不同媒材，這樣只剩深色提醒
    result = make_cover(
        second, FakeCoverRenderer(dark=True), DEFAULT_BRAND,
        subject="s", form="photography", medium="film", style="realism",
    )
    assert result.warnings == ["dark cover next to dark 01-first; max one dark cover in three"]

    light = make_cover(
        third, FakeCoverRenderer(dark=False), DEFAULT_BRAND,
        subject="s", form="sculpture", medium="stone", style="realism",
    )
    assert light.warnings == []

