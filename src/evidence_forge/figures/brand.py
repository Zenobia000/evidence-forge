"""品牌設定：色票、字型、封面用語，從角色的 `brand.yaml` 讀，找不到就用中性預設值。

為什麼獨立成一個模組：配圖腳本原本把色票、字型、封面 prompt 寫死在程式裡，
等於把創作者的品牌放進 repo。機制留在程式，品牌內容（`$EF_DATA/authors/<角色>/brand.yaml`）
留在資料目錄。欄位全貌見套件說明（`evidence_forge.figures`）。
"""

import os
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from matplotlib.ft2font import FT2Font
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StringConstraints,
    ValidationError,
    model_validator,
)

from evidence_forge.jobkey import check_name
from evidence_forge.paths import DataDir

# 色票只收 #RRGGBB，matplotlib 與 PIL 都讀得懂
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]

# 建立樣式時拿來檢查字型有沒有繁體中文字形的幾個常用字
_PROBE_CHARS = "的一是台灣資訊圖"

# 找字型時依序嘗試的副檔名。系統的 .ttc 在 matplotlib 只會用到日文字面，繁中字形會錯，所以不收。
_FONT_EXTENSIONS = (".otf", ".ttf")


class BrandError(ValueError):
    """`brand.yaml` 讀不懂或欄位不合格。"""


class FontNotFoundError(RuntimeError):
    """找不到設定的字型檔。訊息寫明缺哪個字型、找過哪些地方、該放到哪裡。"""


class FontUnusableError(RuntimeError):
    """字型檔找到了，但打不開，或沒有常用繁體中文字的字形（會畫出豆腐方塊）。"""


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
    # 建立樣式時檢查字型有常用繁體中文字的字形；只用拉丁字母的字型才關掉
    require_cjk: bool = True
    # 角色資料夾（不解析符號連結的那個路徑）。不是 brand.yaml 的欄位，是載入時設的：
    # 位在它底下的字型候選，真實位置也必須還在它底下，否則視為不存在。
    _persona_base: Path | None = PrivateAttr(default=None)


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


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def persona_dir(data: DataDir, persona: str) -> Path:
    """`$EF_DATA/authors/<角色>/`。角色名會拼進路徑，先擋掉 `..` 之類。

    這個資料夾必須就是它自己：若是符號連結（指到外面，或指到另一個角色的資料夾）一律拒絕，
    否則一個角色能讀到別人的檔案。`authors/` 本身是連結沒關係（整個資料目錄搬家的情形）。
    """
    check_name("角色", persona)
    path = data.authors / persona
    if path.resolve() != data.authors.resolve() / persona:
        raise BrandError(
            f"角色資料夾 {path} 實際指到 {path.resolve()}，不是 {data.authors} 底下的 {persona}（不接受符號連結）"
        )
    return path


def brand_file(data: DataDir, persona: str) -> Path:
    """`$EF_DATA/authors/<角色>/brand.yaml` 的位置；它本身若是指到角色資料夾之外的符號連結也拒絕。"""
    folder = persona_dir(data, persona)
    path = folder / "brand.yaml"
    if not _inside(path, folder):
        raise BrandError(f"{path} 指到 {path.resolve()}，在角色資料夾 {folder} 之外")
    return path


def _with_persona_fonts(brand: Brand, base: Path, declared: tuple[Path, ...] = ()) -> Brand:
    """字型搜尋路徑：自己宣告的在前，其次是角色資料夾的 `fonts/` 與角色資料夾本身。

    位在角色資料夾底下的候選都受限：真實位置跑出角色資料夾（`fonts/` 是連到別處的連結、
    字型檔是連結、`dirs` 寫了 `../別人/fonts`）就略過。
    """
    font = brand.font.model_copy(update={"dirs": (*declared, base / "fonts", base)})
    font._persona_base = base
    return brand.model_copy(update={"font": font})


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
    return _with_persona_fonts(brand, base, declared)


def load_brand(data: DataDir, persona: str) -> Brand:
    """讀角色的 brand.yaml；檔案不存在就回中性預設值（不是錯誤）。

    沒有 brand.yaml 時字型搜尋路徑仍然包含角色資料夾的 `fonts/`，
    跟有 brand.yaml 的角色一樣：錯誤訊息叫人把字型放在那裡，放了就要找得到。
    """
    path = brand_file(data, persona)
    if not path.is_file():
        return _with_persona_fonts(DEFAULT_BRAND, persona_dir(data, persona))
    return load_brand_file(path)


def resolve_font(spec: FontSpec, weight: Literal["regular", "bold"]) -> Path:
    """找出字型檔；找不到丟 `FontNotFoundError`，訊息講清楚缺什麼、放哪裡。

    `Path.home()` 在呼叫時才查，所以測試改 `HOME` 就能模擬沒裝字型的機器。
    """
    name = spec.regular if weight == "regular" else spec.bold
    given = Path(name).expanduser()
    names = [name] if given.suffix.lower() in _FONT_EXTENSIONS else [name + e for e in _FONT_EXTENSIONS]
    searched: list[str] = []
    # brand.yaml 明確寫的絕對路徑（regular／bold／dirs）照用，不受角色資料夾限制：那是創作者自己指的
    if given.is_absolute():
        for candidate in names:
            searched.append(candidate)
            if Path(candidate).is_file():
                return Path(candidate)
    home_fonts = Path(os.path.expanduser("~")) / ".fonts"
    base = spec._persona_base
    for directory in (*spec.dirs, home_fonts):
        for candidate in names:
            path = directory / candidate
            if not path.is_file():
                searched.append(str(path))
            elif base is not None and path.is_relative_to(base) and not _inside(path, base):
                searched.append(f"{path}（符號連結指到角色資料夾之外，略過）")
            else:
                return path
    tried = "\n".join(f"  - {p}" for p in searched)
    raise FontNotFoundError(
        f"找不到字型「{spec.family}」（{weight}：{name}）。找過這些位置：\n{tried}\n"
        f"請把 {names[0]} 放進 {home_fonts}，或放在角色資料夾的 fonts/ 底下，"
        "或在 brand.yaml 的 font.dirs 指定資料夾。"
        "Noto Sans TC 可以從 https://github.com/notofonts/noto-cjk 下載（OFL 授權）。"
    )


def check_font(path: Path, spec: FontSpec) -> None:
    """確認字型檔真的打得開，而且（`require_cjk` 時）有常用繁體中文字的字形。

    只看檔案存在不夠：壞掉的檔案要到畫圖才炸，缺字的字型不會報錯，只會畫出豆腐方塊。
    """
    try:
        face = FT2Font(str(path))
    except (OSError, RuntimeError, ValueError) as exc:
        raise FontUnusableError(f"字型「{spec.family}」的檔案 {path} 打不開：{exc}") from exc
    if not spec.require_cjk:
        return
    missing = [c for c in _PROBE_CHARS if face.get_char_index(ord(c)) == 0]
    if missing:
        raise FontUnusableError(
            f"字型檔 {path} 沒有這些常用繁體中文字的字形：{''.join(missing)}。"
            f"請換成「{spec.family}」或其他支援繁體中文的字型；"
            "只用拉丁字母的字型要在 brand.yaml 設 font.require_cjk: false。"
        )
