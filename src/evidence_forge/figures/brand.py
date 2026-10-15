"""品牌設定：色票、字型、封面用語，從角色的 `brand.yaml` 讀，找不到就用中性預設值。

為什麼獨立成一個模組：配圖腳本原本把色票、字型、封面 prompt 寫死在程式裡，
等於把創作者的品牌放進 repo。機制留在程式，品牌內容（`$EF_DATA/authors/<角色>/brand.yaml`）
留在資料目錄。欄位全貌見套件說明（`evidence_forge.figures`）。
"""

import os
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from evidence_forge.jobkey import check_name
from evidence_forge.paths import DataDir

# 色票只收 #RRGGBB，matplotlib 與 PIL 都讀得懂
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]

# 找字型時依序嘗試的副檔名。系統的 .ttc 在 matplotlib 只會用到日文字面，繁中字形會錯，所以不收。
_FONT_EXTENSIONS = (".otf", ".ttf")


class BrandError(ValueError):
    """`brand.yaml` 讀不懂或欄位不合格。"""


class FontNotFoundError(RuntimeError):
    """找不到設定的字型檔。訊息寫明缺哪個字型、找過哪些地方、該放到哪裡。"""


class _Strict(BaseModel):
    # 多出不認得的欄位一律拒絕（同 models.py）；模型不可變
    model_config = ConfigDict(extra="forbid", frozen=True)


class Palette(_Strict):
    """七個顏色。預設是中性的灰階加一個通用重點色。"""

    paper: HexColor = "#F5F5F5"  # 圖的底色
    ink: HexColor = "#1F1F1F"  # 標題、主要線條
    graphite: HexColor = "#4A4A4A"  # 次要文字
    accent: HexColor = "#2F6DB5"  # 一張圖只標一個重點
    warn: HexColor = "#B5483A"  # 只標失敗、衰退、風險
    mist: HexColor = "#C8C8C8"  # 沒有被強調的資料
    grid: HexColor = "#E3E3E3"  # 格線


class FontSpec(_Strict):
    """字型：名稱只用在錯誤訊息；regular／bold 是檔名（不含副檔名）或路徑。"""

    family: str = "Noto Sans TC"
    regular: str = "NotoSansTC-Regular"
    bold: str = "NotoSansTC-Bold"
    # 另外找字型的資料夾，排在 `~/.fonts` 前面
    dirs: tuple[Path, ...] = ()


class MediumSpec(_Strict):
    """媒材：可以搭配哪幾種形式，加上給生圖模型的描述。"""

    forms: tuple[str, ...]
    description: str


class PresetSpec(_Strict):
    """固定組合：三層加一段完整的開頭，用來還原舊文章只記了預設名稱的封面。"""

    form: str
    medium: str
    style: str
    preamble: str


def _default_forms() -> dict[str, str]:
    return {
        "painting": "A painting composed to occupy the entire image, with no border around it.",
        "photography": "A photograph in the manner of an art catalogue, lit from one direction.",
        "sculpture": "One sculpture standing alone in an empty, quiet room.",
    }


def _default_media() -> dict[str, MediumSpec]:
    return {
        "oil": MediumSpec(forms=("painting",), description="Oil paint, with the strokes still showing."),
        "ink-wash": MediumSpec(
            forms=("painting",), description="Diluted black ink on paper, with soft tonal steps."
        ),
        "film": MediumSpec(
            forms=("photography",), description="Analogue film look, with visible grain."
        ),
        "stone": MediumSpec(forms=("sculpture",), description="Worked stone, heavy and solid."),
    }


def _default_styles() -> dict[str, str]:
    return {
        "realism": "Realism: things look as they do in life.",
        "minimalism": "Minimalism: the fewest elements that still hold together.",
        "abstract": "Abstract: built from colour, line and shape, not from recognisable objects.",
    }


class CoverVocabulary(_Strict):
    """封面三層（形式 form x 媒材 medium x 風格 style）的詞彙與共同限制。

    預設只有一小組中性的詞，讓沒有 brand.yaml 的機器也能組出 prompt；
    創作者自己的詞彙寫在 brand.yaml，整張字典一起換。
    """

    forms: dict[str, str] = Field(default_factory=_default_forms)
    media: dict[str, MediumSpec] = Field(default_factory=_default_media)
    styles: dict[str, str] = Field(default_factory=_default_styles)
    presets: dict[str, PresetSpec] = Field(default_factory=dict)
    shared_rules: str = (
        "Keep to a single subject with an obvious focal point, and leave quiet empty space "
        "where a title could be placed. Use a nearly single-colour palette plus at most one "
        "accent. The image must contain no writing of any kind (letters, numbers, logos) "
        "and no people."
    )
    market_preamble: str = (
        "A documentary-style editorial photograph of a real scene from the market under "
        "discussion, lit from a single source."
    )
    # 市場觀察封面用的限制；不寫就沿用 shared_rules
    market_rules: str | None = None

    @model_validator(mode="after")
    def _references_exist(self) -> Self:
        for name, medium in self.media.items():
            for form in medium.forms:
                if form not in self.forms:
                    raise ValueError(f"媒材 {name} 搭配的形式 {form!r} 不在 forms 裡")
        for name, preset in self.presets.items():
            if preset.form not in self.forms:
                raise ValueError(f"預設 {name} 的形式 {preset.form!r} 不在 forms 裡")
            if preset.medium not in self.media:
                raise ValueError(f"預設 {name} 的媒材 {preset.medium!r} 不在 media 裡")
            if preset.style not in self.styles:
                raise ValueError(f"預設 {name} 的風格 {preset.style!r} 不在 styles 裡")
        return self


class Brand(_Strict):
    """一個角色的品牌設定。全部欄位都有中性預設值，`Brand()` 就是沒有 brand.yaml 的樣子。"""

    name: str = ""
    palette: Palette = Field(default_factory=Palette)
    font: FontSpec = Field(default_factory=FontSpec)
    cover: CoverVocabulary = Field(default_factory=CoverVocabulary)


DEFAULT_BRAND = Brand()


def brand_file(data: DataDir, persona: str) -> Path:
    """`$EF_DATA/authors/<角色>/brand.yaml` 的位置。角色名會拼進路徑，先擋掉 `..` 之類。"""
    check_name("角色", persona)
    return data.authors / persona / "brand.yaml"


def load_brand_file(path: Path) -> Brand:
    """讀一個 brand.yaml。檔案裡的相對字型資料夾以它所在的資料夾為準。

    角色資料夾本身與其中的 `fonts/` 會自動加進字型搜尋路徑（排在 `dirs` 後面）。
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise BrandError(f"{path} 不是合法的 YAML：{exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise BrandError(f"{path} 最上層必須是欄位對照（mapping）")
    try:
        brand = Brand.model_validate(raw)
    except ValidationError as exc:
        raise BrandError(f"{path} 的欄位不合格：\n{exc}") from exc

    base = path.parent
    declared = tuple(
        d if (d := p.expanduser()).is_absolute() else base / d for p in brand.font.dirs
    )
    font = brand.font.model_copy(update={"dirs": (*declared, base / "fonts", base)})
    return brand.model_copy(update={"font": font})


def load_brand(data: DataDir, persona: str) -> Brand:
    """讀角色的 brand.yaml；檔案不存在就回中性預設值（不是錯誤）。"""
    path = brand_file(data, persona)
    if not path.is_file():
        return DEFAULT_BRAND
    return load_brand_file(path)


def resolve_font(spec: FontSpec, weight: Literal["regular", "bold"]) -> Path:
    """找出字型檔；找不到丟 `FontNotFoundError`，訊息講清楚缺什麼、放哪裡。

    `Path.home()` 在呼叫時才查，所以測試改 `HOME` 就能模擬沒裝字型的機器。
    """
    name = spec.regular if weight == "regular" else spec.bold
    given = Path(name).expanduser()
    names = [name] if given.suffix.lower() in _FONT_EXTENSIONS else [name + e for e in _FONT_EXTENSIONS]
    searched: list[Path] = []
    if given.is_absolute():
        for candidate in names:
            searched.append(Path(candidate))
            if Path(candidate).is_file():
                return Path(candidate)
    home_fonts = Path(os.path.expanduser("~")) / ".fonts"
    for directory in (*spec.dirs, home_fonts):
        for candidate in names:
            path = directory / candidate
            searched.append(path)
            if path.is_file():
                return path
    tried = "\n".join(f"  - {p}" for p in searched)
    raise FontNotFoundError(
        f"找不到字型「{spec.family}」（{weight}：{name}）。找過這些位置：\n{tried}\n"
        f"請把 {names[0]} 放進 {home_fonts}，或放在角色資料夾的 fonts/ 底下，"
        "或在 brand.yaml 的 font.dirs 指定資料夾。"
        "Noto Sans TC 可以從 https://github.com/notofonts/noto-cjk 下載（OFL 授權）。"
    )
