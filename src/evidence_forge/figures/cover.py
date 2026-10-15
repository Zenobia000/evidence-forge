"""封面：形式 x 媒材 x 風格 三層組合、prompt、裁切成 16:9、相鄰文章的提醒。

三層各管一件事：形式（form）決定作品怎麼存在（繪畫、雕塑、攝影…），媒材（medium）決定用什麼做
（油畫、石頭、底片…，每種只配得上某幾種形式），風格（style）決定怎麼表現（寫實、極簡…）。
詞彙與每一層的英文描述、共同限制都來自 `Brand.cover`（brand.yaml），這個模組只管組合的機制。

真的畫圖不在這裡：這個模組只認 `CoverRenderer` 介面（給 prompt，拿回 PNG 位元組）。
測試用 `FakeCoverRenderer`；接上圖像模型由創作流程 7b 經閘道做。
"""

import io
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageStat

from evidence_forge.figures.brand import Brand, CoverVocabulary

# 平均亮度（0～255）低於這個值就算深色為主的封面
DARK_LUMA = 85
# 生圖尺寸與品質：交給 renderer；最後裁成 16:9 的 1600 x 900
RENDER_SIZE = "1536x1024"
RENDER_QUALITY = "high"
COVER_WIDTH, COVER_HEIGHT = 1600, 900
WEBP_QUALITY = 82


class CoverError(ValueError):
    """封面的三層選錯了（沒有這個詞，或媒材配不上形式）。"""


class CoverRenderer(Protocol):
    """把 prompt 變成一張圖的東西。回傳 PNG 位元組。"""

    def render(self, prompt: str, *, size: str, quality: str) -> bytes: ...


@dataclass
class FakeCoverRenderer:
    """測試用：不呼叫任何模型，回一張單色漸層的 PNG，並記下收到的 prompt。"""

    dark: bool = False
    width: int = 1536
    height: int = 1024
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    def render(self, prompt: str, *, size: str, quality: str) -> bytes:
        self.calls.append((prompt, size, quality))
        base = 20 if self.dark else 200
        image = Image.new("RGB", (self.width, self.height))
        for y in range(self.height):
            value = base + (y * 20) // self.height
            image.paste((value, value, value), (0, y, self.width, y + 1))
        out = io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue()


@dataclass(frozen=True)
class CoverLayers:
    form: str
    medium: str
    style: str
    preamble: str


def resolve_layers(
    vocab: CoverVocabulary,
    *,
    preset: str | None = None,
    form: str | None = None,
    medium: str | None = None,
    style: str | None = None,
) -> CoverLayers:
    """給預設名稱，或給三層全部；回傳三層與 prompt 的開頭。選錯丟 `CoverError`。"""
    if preset:
        if preset not in vocab.presets:
            raise CoverError(f"沒有這個預設：{preset!r}（有：{', '.join(sorted(vocab.presets)) or '無'}）")
        p = vocab.presets[preset]
        return CoverLayers(p.form, p.medium, p.style, p.preamble)
    if not (form and medium and style):
        raise CoverError("要給 preset，或同時給 form、medium、style")
    if form not in vocab.forms:
        raise CoverError(f"沒有這個形式：{form!r}（有：{', '.join(sorted(vocab.forms))}）")
    if medium not in vocab.media:
        raise CoverError(f"沒有這個媒材：{medium!r}（有：{', '.join(sorted(vocab.media))}）")
    if style not in vocab.styles:
        raise CoverError(f"沒有這個風格：{style!r}（有：{', '.join(sorted(vocab.styles))}）")
    allowed = vocab.media[medium].forms
    if form not in allowed:
        raise CoverError(f"媒材 {medium} 只配 {', '.join(allowed)}，不配 {form}")
    preamble = f"{vocab.forms[form]} {vocab.media[medium].description} {vocab.styles[style]}"
    return CoverLayers(form, medium, style, preamble)


def build_prompt(vocab: CoverVocabulary, layers: CoverLayers, subject: str, market: bool = False) -> str:
    """組 prompt。市場觀察封面直接拍市場裡的物件，開頭與限制換成 market 那組。"""
    if market:
        rules = vocab.market_rules if vocab.market_rules is not None else vocab.shared_rules
        return f"{vocab.market_preamble} Subject: {subject} {rules}"
    return f"{layers.preamble} Subject: {subject} {vocab.shared_rules}"


def crop_16_9(raw: bytes) -> Image.Image:
    """置中裁成 16:9 再縮成 1600 x 900。來源比 16:9 寬就裁左右，否則裁上下。"""
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = im.size
    if w * 9 > h * 16:  # 比 16:9 寬
        target_w = round(h * 16 / 9)
        left = (w - target_w) // 2
        box = (left, 0, left + target_w, h)
    else:
        target_h = round(w * 9 / 16)
        top = (h - target_h) // 2
        box = (0, top, w, top + target_h)
    return im.crop(box).resize((COVER_WIDTH, COVER_HEIGHT), Image.Resampling.LANCZOS)


def is_dark(path: Path) -> bool:
    with Image.open(path) as im:
        return bool(ImageStat.Stat(im.convert("L")).mean[0] < DARK_LUMA)


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _own_file(path: Path, root: Path) -> bool:
    """是檔案，而且真實位置還在 root 底下（符號連結指到外面的不算）。"""
    return path.is_file() and _inside(path, root)


def _write_nofollow(path: Path, data: bytes) -> None:
    """寫檔，但不跟著既有的符號連結走：目標若是符號連結就拒絕，不會寫到連結指向的地方。"""
    if path.is_symlink():
        raise CoverError(f"{path} 是符號連結，不寫入")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o644)
    except OSError as exc:
        raise CoverError(f"無法寫入 {path}：{exc}") from exc
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _encode(image: Image.Image, fmt: str, **options: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    return buffer.getvalue()


def siblings_of(article_dir: Path) -> list[Path]:
    """同一層、有 article.md 的文章資料夾，依名稱排序（即文章順序）。符號連結的資料夾不算。"""
    return sorted(
        d
        for d in article_dir.parent.iterdir()
        if d.is_dir() and not d.is_symlink() and _own_file(d / "article.md", d)
    )


def _position(article_dir: Path) -> tuple[list[Path], int] | None:
    """文章在同層文章裡的順序位置；找不到回 None。用真實路徑比，`./a/../b` 之類也認得。"""
    siblings = siblings_of(article_dir)
    real = article_dir.resolve()
    for i, sibling in enumerate(siblings):
        if sibling.resolve() == real:
            return siblings, i
    return None


def layers_of(vocab: CoverVocabulary, article_dir: Path) -> tuple[str, str] | None:
    """一篇文章封面的 (形式, 媒材)，從 front matter 讀；舊的預設名稱會還原成三層。"""
    md = article_dir / "article.md"
    if not _own_file(md, article_dir):
        return None
    text = md.read_text(encoding="utf-8")
    medium = re.search(r'^cover_medium:\s*"([^"]+)"', text, re.M)
    if not medium:
        return None
    if medium.group(1) in vocab.presets:
        preset = vocab.presets[medium.group(1)]
        return preset.form, preset.medium
    form = re.search(r'^cover_form:\s*"([^"]+)"', text, re.M)
    return (form.group(1) if form else "", medium.group(1))


def neighbour_warnings(vocab: CoverVocabulary, article_dir: Path, form: str, medium: str) -> list[str]:
    """前後一篇的封面若形式與媒材都一樣就提醒：相鄰文章至少換一個。"""
    position = _position(article_dir)
    if position is None:
        return ["article dir is not among its sibling articles; the neighbour check was skipped"]
    siblings, i = position
    return [
        f"neighbour {siblings[j].name} also uses {form}/{medium}; change the form or the medium"
        for j in (i - 1, i + 1)
        if 0 <= j < len(siblings) and layers_of(vocab, siblings[j]) == (form, medium)
    ]


def dark_warnings(article_dir: Path) -> list[str]:
    """這張和前兩篇之一都是深色為主就提醒：連續三篇最多一張深色。"""
    if not is_dark(article_dir / "images/cover.webp"):
        return []
    position = _position(article_dir)
    if position is None:
        return ["article dir is not among its sibling articles; the dark-cover check was skipped"]
    siblings, i = position
    out: list[str] = []
    for j in (i - 2, i - 1):
        if j < 0:
            continue
        cover = siblings[j] / "images/cover.webp"
        if _own_file(cover, siblings[j]) and is_dark(cover):
            out.append(f"dark cover next to dark {siblings[j].name}; max one dark cover in three")
    return out


def set_front_matter(article_dir: Path, layers: CoverLayers) -> bool:
    """把 cover_form／cover_medium／cover_style 寫進 article.md 的 front matter。

    有 `cover:` 那行就接在後面，沒有就放在 front matter 結尾前；沒有 front matter 就不寫，
    回傳 False。鍵名用底線：發布腳本只認 `\\w+`。article.md 若是符號連結就拒絕寫入。
    """
    md = article_dir / "article.md"
    text = md.read_text(encoding="utf-8")
    text = re.sub(r"^cover_(form|medium|style):.*\n", "", text, flags=re.M)
    block = f'cover_form: "{layers.form}"\ncover_medium: "{layers.medium}"\ncover_style: "{layers.style}"'
    if re.search(r"^cover:.*$", text, re.M):
        text = re.sub(r"^(cover:.*)$", lambda m: f"{m.group(1)}\n{block}", text, count=1, flags=re.M)
    else:
        m = re.match(r"---\n[\s\S]*?\n(?=---\n)", text)
        if not m:
            return False
        text = text[: m.end()] + block + "\n" + text[m.end() :]
    _write_nofollow(md, text.encode("utf-8"))
    return True


def _check_targets(article_dir: Path) -> Path:
    """寫任何東西之前，確認要寫的位置都在文章資料夾裡，而且不是符號連結；回傳 images/。

    `images/` 若是指到外面的符號連結，寫進去的檔案就跑出文章資料夾了；
    已經存在的 `cover*.png`／`cover.webp`／`article.md` 若是符號連結，寫入會改到別處的檔案。
    在呼叫 renderer 之前就擋，不花一次生圖的錢。
    """
    images = article_dir / "images"
    if images.is_symlink() or (images.exists() and not _inside(images, article_dir)):
        raise CoverError(f"{images} 是符號連結或指到文章資料夾之外，不寫入")
    for target in (
        images / "cover-raw.png",
        images / "cover.png",
        images / "cover.webp",
        article_dir / "article.md",
    ):
        if target.is_symlink():
            raise CoverError(f"{target} 是符號連結，不寫入")
    return images


@dataclass(frozen=True)
class CoverResult:
    layers: CoverLayers
    prompt: str
    files: list[Path]
    warnings: list[str]


def make_cover(
    article_dir: Path,
    renderer: CoverRenderer,
    brand: Brand,
    *,
    subject: str,
    preset: str | None = None,
    form: str | None = None,
    medium: str | None = None,
    style: str | None = None,
    market: bool = False,
) -> CoverResult:
    """產生一張封面。

    順序：選三層 → 組 prompt → 檢查寫入位置 → 前後篇撞型提醒 → 交給 renderer → 存原圖
    `cover-raw.png`、裁成 16:9 存 `cover.png` 與 `cover.webp` → 深色比例提醒 → 把三層寫進 front matter。
    提醒收在 `result.warnings`，不印也不擋；畫出來的圖要人自己看過（有字、人臉、圖解感就重畫）。

    還沒有 `article.md` 時照樣畫封面（先有圖、後有文章），但前後篇比對、深色比例、
    front matter 三件事都做不了，`warnings` 會明說跳過了什麼，不會默默當作沒問題。
    寫入位置有符號連結跑出文章資料夾，或選錯三層，都在呼叫 renderer 之前就丟 `CoverError`。
    """
    layers = resolve_layers(brand.cover, preset=preset, form=form, medium=medium, style=style)
    prompt = build_prompt(brand.cover, layers, subject, market)
    images = _check_targets(article_dir)
    has_article = (article_dir / "article.md").exists()
    if has_article:
        warnings = neighbour_warnings(brand.cover, article_dir, layers.form, layers.medium)
    else:
        warnings = [
            "no article.md: the neighbour check, the dark-cover check and the front matter write were skipped"
        ]

    raw = renderer.render(prompt, size=RENDER_SIZE, quality=RENDER_QUALITY)
    images.mkdir(parents=True, exist_ok=True)
    if not _inside(images, article_dir):  # 檢查之後到建立之前被換成連結的情形
        raise CoverError(f"{images} 指到文章資料夾之外，不寫入")
    raw_path = images / "cover-raw.png"
    png, webp = images / "cover.png", images / "cover.webp"
    cropped = crop_16_9(raw)
    _write_nofollow(raw_path, raw)
    _write_nofollow(png, _encode(cropped, "PNG"))
    _write_nofollow(webp, _encode(cropped, "WEBP", quality=WEBP_QUALITY))

    if has_article:
        warnings += dark_warnings(article_dir)
        if not set_front_matter(article_dir, layers):
            warnings.append("article.md has no front matter; cover layers were not recorded")
    return CoverResult(layers, prompt, [raw_path, png, webp], warnings)
