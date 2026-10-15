"""把創作者自己的寫作系統拆進一個角色資料夾（#106、ADR-0026 §3.3）。

來源是一個 Claude Code skill（創作者給的 zip，或解開後的資料夾），樣子是：

    .claude/skills/<名字>/
      SKILL.md                         # 編號的節：§0 任務、§2 品牌主張、§4 聲音……
      references/genres/<slug>.md      # 每種文體的寫法、價值、CTA；README.md 有主題表
      references/craft/*.md            # 開場、標題、金句與收尾、圖文編排、配圖……
      references/strategy/*.md         # 各主題的主張、標題範例
      references/lessons-learnt.md     # 退件紀錄（表格，新的在上）
      scripts/chart_style.py           # 色票（字面值）
      scripts/make_cover.py            # 封面三層用語（字面值）

寫成 `$EF_DATA/authors/<角色>/` 的這幾個檔（每個角色同一種樣子）：
`voice.md`、`genres/<slug>.md`、`craft/*.md`、`themes.md`、`lessons.md`、`brand.yaml`。

規則：
- **只讀不跑**：zip 不解開到硬碟，直接讀成員；腳本只用 `ast` 取字面值，不 import、不執行。
- **先備份再換**：角色資料夾已經有東西，就整份複製到 `authors/.backup/<角色>-<時間>/`，
  之後才換掉匯入要寫的檔。匯入沒寫到的檔原樣留著；什麼都不刪。
- **品牌內容不進 repo**：這支程式只知道來源的檔案結構；內容只在 zip 與 `EF_DATA`。
  命令列只印檔名與備份位置，不印內容（輸出會貼進報告與 PR）。
"""

import argparse
import ast
import os
import re
import shutil
import sys
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from evidence_forge.figures.brand import Brand, persona_dir
from evidence_forge.harness import default_harness
from evidence_forge.paths import DataDir, resolve_data_dir
from evidence_forge.ui.format import GENRE_NAME

# 認出寫作系統的根目錄：有這個檔、而且旁邊有 SKILL.md
MARKER = "references/genres/README.md"
SKILL = "SKILL.md"
# 只讀這幾種檔；單檔太大就跳過（防一個意外的大檔塞爆記憶體）
_TEXT_SUFFIXES = (".md", ".py", ".json")
_MAX_FILE_BYTES = 2_000_000

# SKILL.md 裡屬於「這個角色怎麼說話」的編號節（ADR-0026 §3.3 `voice.md`）
_VOICE_SECTIONS = ("0", "2", "4", "5", "6", "15")
# §12 品質關卡裡只取句型那一關：可以用程式擋的寫作規則屬於聲音（ADR-0025 §3.7）
_VOICE_GATE = ("12", "AI-Smell Gate")
# craft：來源路徑 → 角色資料夾裡的檔名（一個主題一份）
_CRAFT = {
    "references/craft/openings.md": "openings.md",
    "references/craft/titles.md": "titles.md",
    "references/craft/golden-lines-and-endings.md": "golden-lines-and-endings.md",
    "references/craft/layout.md": "layout.md",
    "references/craft/figures.md": "figures.md",
    "references/craft/charts.md": "charts.md",
    "references/craft/chart-selection.md": "chart-selection.md",
    "references/craft/chart-brief.md": "chart-brief.md",
    "references/strategy/examples.md": "title-examples.md",
}
_CRAFT_VOICE = "references/craft/voice.md"
_STRATEGY = "references/strategy/content-strategy.md"
_LESSONS = "references/lessons-learnt.md"
_CHART_STYLE = "scripts/chart_style.py"
_MAKE_COVER = "scripts/make_cover.py"

# 只增不改的檔：已經有內容就只把新的條目接在後面（ADR-0026 §3.3：lessons 之後只有閘道寫）
_APPEND_ONLY = ("lessons.md",)
_BACKUP_DIR = ".backup"

_FENCE = re.compile(r"^(```|~~~)")
_HEADING = re.compile(r"^(#{1,6}) ")
_THEME_ROW = re.compile(r"^\|\s*`([a-z0-9-]+)`\s*\|")


class PersonaImportError(ValueError):
    """來源讀不懂、或角色資料夾不能安全地寫。"""


@dataclass(frozen=True)
class SourceTree:
    """寫作系統裡的文字檔：相對於 skill 根目錄的路徑 → 內容。"""

    origin: str
    files: dict[str, str]

    def get(self, relative: str) -> str | None:
        return self.files.get(relative)


@dataclass(frozen=True)
class ImportResult:
    backup: Path | None
    written: list[str]
    # 沒寫的檔與原因（例：lessons.md 沒有新的條目）
    notes: list[str] = field(default_factory=list)


# --- 讀來源 -------------------------------------------------------------------


def _skill_root(names: Sequence[str]) -> str:
    """從所有檔名找出寫作系統的根目錄（以 `/` 結尾，或空字串表示就在最上層）。"""
    roots = sorted(
        {
            name[: -len(MARKER)]
            for name in names
            if (name == MARKER or name.endswith("/" + MARKER))
            and name[: -len(MARKER)] + SKILL in names
        }
    )
    if not roots:
        raise PersonaImportError(f"找不到寫作系統：要有 {MARKER}，而且旁邊有 {SKILL}")
    if len(roots) > 1:
        raise PersonaImportError(f"來源裡有兩份以上的寫作系統：{', '.join(r or '.' for r in roots)}")
    return roots[0]


def _wanted(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return bool(parts) and ".." not in parts and relative.endswith(_TEXT_SUFFIXES)


def read_source(path: Path) -> SourceTree:
    """讀 zip 或資料夾裡的寫作系統。zip 不解開，只讀成員；不跟隨符號連結。"""
    if path.is_file():
        if not zipfile.is_zipfile(path):
            raise PersonaImportError(f"{path} 不是 zip，也不是資料夾")
        with zipfile.ZipFile(path) as archive:
            members = {
                info.filename: info for info in archive.infolist() if not info.is_dir()
            }
            root = _skill_root(list(members))
            files: dict[str, str] = {}
            for name, info in members.items():
                relative = name[len(root) :]
                if not name.startswith(root) or not _wanted(relative):
                    continue
                if info.file_size > _MAX_FILE_BYTES:
                    continue
                files[relative] = archive.read(info).decode("utf-8")
        return SourceTree(origin=path.name, files=files)
    if path.is_dir():
        found = {
            p.relative_to(path).as_posix(): p
            for p in path.rglob("*")
            if p.is_file() and not p.is_symlink()
        }
        root = _skill_root(list(found))
        files = {}
        for name, file in found.items():
            relative = name[len(root) :]
            if not name.startswith(root) or not _wanted(relative):
                continue
            if file.stat().st_size > _MAX_FILE_BYTES:
                continue
            files[relative] = file.read_text(encoding="utf-8")
        return SourceTree(origin=path.name, files=files)
    raise PersonaImportError(f"找不到 {path}")


# --- Markdown 的小工具 ----------------------------------------------------------


def _lines_outside_fences(text: str) -> list[tuple[str, bool]]:
    """每一行與它在不在程式碼區塊裡（區塊裡的 `#` 不是標題）。"""
    marked: list[tuple[str, bool]] = []
    inside = False
    for line in text.split("\n"):
        if _FENCE.match(line):
            marked.append((line, True))
            inside = not inside
            continue
        marked.append((line, inside))
    return marked


def _section(text: str, level: int, matches: Callable[[str], bool]) -> str | None:
    """取出某一層標題的一節（含標題那一行），到下一個同層或更高層的標題為止。"""
    out: list[str] = []
    capturing = False
    for line, fenced in _lines_outside_fences(text):
        heading = None if fenced else _HEADING.match(line)
        if heading is not None and len(heading.group(1)) <= level:
            if capturing:
                break
            if len(heading.group(1)) == level and matches(line[level + 1 :].strip()):
                capturing = True
        if capturing:
            out.append(line)
    return "\n".join(out).strip() if out else None


def _numbered(text: str, number: str) -> str | None:
    """SKILL.md 的編號節：`## 4. Author Voice`。"""
    return _section(text, 2, lambda title: title.startswith(f"{number}. "))


def _demote(text: str, by: int = 1) -> str:
    """每個標題降 `by` 級（程式碼區塊裡的不動）。"""
    out: list[str] = []
    for line, fenced in _lines_outside_fences(text):
        heading = None if fenced else _HEADING.match(line)
        if heading is not None and len(heading.group(1)) + by <= 6:
            line = "#" * by + line
        out.append(line)
    return "\n".join(out)


def _body_without_title(text: str) -> str:
    """去掉第一個一級標題與緊接著的引言（`> ...`），留下其餘。"""
    lines = text.strip().split("\n")
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    while lines and (not lines[0].strip() or lines[0].startswith(">")):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _frontmatter(fields: dict[str, Any], comment: str) -> str:
    header = yaml.safe_dump(fields, allow_unicode=True, sort_keys=False, width=10_000)
    return f"---\n# {comment}\n{header}---\n\n"


# --- 拆成角色資料夾的檔 ---------------------------------------------------------


def _voice(source: SourceTree, persona: str, name: str, default_genre: str, stamp: str) -> str:
    skill = source.get(SKILL) or ""
    parts = [s for n in _VOICE_SECTIONS if (s := _numbered(skill, n)) is not None]
    if not parts:
        # 換錯來源時寧可停下來，也不要用一份只有檔頭的聲音蓋掉原本的
        raise PersonaImportError(
            f"{SKILL} 裡找不到聲音的節（§{'、§'.join(_VOICE_SECTIONS)}），不寫 voice.md"
        )
    gates = _numbered(skill, _VOICE_GATE[0])
    gate = _section(gates, 3, lambda t: t == _VOICE_GATE[1]) if gates else None
    if gate is not None:
        parts.append("## 句型與 AI 味\n\n" + _body_after_heading(gate))
    craft_voice = source.get(_CRAFT_VOICE)
    if craft_voice is not None:
        parts.append("## 聲音細節\n\n" + _demote(_body_without_title(craft_voice)))
    fields = {
        "id": persona,
        "name": name or persona,
        "default_genre": default_genre,
        "samples": [],
        "calibrated": True,
    }
    comment = f"角色的聲音（ADR-0026 §3.3）。由 scripts/import_persona.py 從 {source.origin} 匯入（{stamp}）"
    return _frontmatter(fields, comment) + "\n\n".join(parts) + "\n"


def _body_after_heading(section: str) -> str:
    return section.split("\n", 1)[1].strip() if "\n" in section else ""


def _genre_style(text: str, persona: str, slug: str, stamp: str) -> str:
    body = _body_without_title(text)
    # 「<品牌> 要加的價值」→ 每個角色同一個節名（ADR-0026 §3.3）
    body = re.sub(r"^## .*要加的價值[ \t]*$", "## 要多帶的價值", body, flags=re.MULTILINE)
    fields = {
        "author": persona,
        "genre": slug,
        "samples": [],
        # 空檔、或來源自己寫「尚無已發布文章」的文體只是起始規範，還沒校準
        "calibrated": bool(body) and "尚無已發布文章" not in text,
    }
    comment = f"角色的文體風格（ADR-0026 §3.3）。由 scripts/import_persona.py 匯入（{stamp}）"
    return _frontmatter(fields, comment) + body + "\n"


def _themes(source: SourceTree, persona: str, stamp: str, known: set[str]) -> str | None:
    readme = source.get(MARKER) or ""
    table = _section(readme, 2, lambda t: "主題" in t and "theme" in t)
    if table is None:
        return None
    kept: list[str] = []
    slugs: list[str] = []
    for line in _body_after_heading(table).split("\n"):
        row = _THEME_ROW.match(line)
        if row is not None:
            if row.group(1) not in known:
                continue
            slugs.append(row.group(1))
        kept.append(line)
    parts = ["## 用哪幾個主題\n\n" + "\n".join(kept).strip()]
    strategy = source.get(_STRATEGY)
    claims = _section(strategy, 2, lambda t: t.startswith("主題的典型主張")) if strategy else None
    if claims is not None:
        parts.append("## 對每個主題的主張\n\n" + _body_after_heading(claims))
    comment = f"角色對主題的看法（ADR-0026 §3.2、§3.3）。由 scripts/import_persona.py 匯入（{stamp}）"
    return _frontmatter({"author": persona, "themes": slugs}, comment) + "\n\n".join(parts) + "\n"


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _lessons(source: SourceTree, stamp: str) -> str | None:
    text = source.get(_LESSONS)
    if text is None:
        return None
    rows = [_cells(line) for line in text.split("\n") if line.startswith("|")]
    entries: list[str] = []
    for cells in rows:
        if len(cells) != 5 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
            continue  # 表頭、分隔線
        date, article, happened, changed, where = cells
        entries.append(f"- {date}｜第 {article} 篇｜{happened} → {changed}（規則在 {where}）")
    entries.reverse()  # 來源新的在上；這裡一筆一條、新的在後
    header = (
        "# 退件與指正累積的規則\n\n"
        "<!-- ADR-0026 §3.3：一筆一條，新的在後；訪談與寫稿開工前讀最近十筆。之後只有閘道寫（#114）。"
        f"這一份由 scripts/import_persona.py 從 {source.origin} 匯入（{stamp}）。 -->\n\n"
    )
    return header + "\n".join(entries) + "\n"


def _literal_assignments(code: str) -> dict[str, Any]:
    """模組最上層 `名字 = 字面值` 的值；`X = Y.replace("a", "b")`（Y 是字面值）也算。不執行任何程式。"""
    found: dict[str, Any] = {}
    for node in ast.parse(code).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        try:
            found[target.id] = ast.literal_eval(value)
            continue
        except ValueError:
            pass
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "replace"
            and isinstance(value.func.value, ast.Name)
            and isinstance(found.get(value.func.value.id), str)
            and len(value.args) == 2
            and not value.keywords
        ):
            old, new = (ast.literal_eval(arg) for arg in value.args)
            if isinstance(old, str) and isinstance(new, str):
                found[target.id] = found[value.func.value.id].replace(old, new)
    return found


def _brand_name(code: str) -> str:
    """圖頁尾的品牌名：`*.text(x, y, "<字面值>", ...)` 的第三個參數。沒有就空字串。"""
    for node in ast.walk(ast.parse(code)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "text"
            and len(node.args) >= 3
            and isinstance(node.args[2], ast.Constant)
            and isinstance(node.args[2].value, str)
        ):
            return node.args[2].value
    return ""


def _palette(values: dict[str, Any]) -> dict[str, str] | None:
    """色票：最上層一個鍵包含 paper、ink、accent 的字典。"""
    wanted = set(Brand().palette.model_dump())
    for value in values.values():
        if isinstance(value, dict) and {"paper", "ink", "accent"} <= set(value):
            return {k: v for k, v in value.items() if k in wanted}
    return None


def _brand(source: SourceTree) -> tuple[dict[str, Any], str]:
    raw: dict[str, Any] = {}
    name = ""
    chart = source.get(_CHART_STYLE)
    if chart is not None:
        name = _brand_name(chart)
        palette = _palette(_literal_assignments(chart))
        if name:
            raw["name"] = name
        if palette:
            raw["palette"] = palette
    cover_code = source.get(_MAKE_COVER)
    if cover_code is not None:
        values = _literal_assignments(cover_code)
        cover: dict[str, Any] = {}
        if isinstance(values.get("FORMS"), dict):
            cover["forms"] = values["FORMS"]
        if isinstance(values.get("MEDIA"), dict):
            cover["media"] = {
                key: {"forms": list(forms), "description": description}
                for key, (forms, description) in values["MEDIA"].items()
            }
        if isinstance(values.get("STYLES"), dict):
            cover["styles"] = values["STYLES"]
        if isinstance(values.get("PRESETS"), dict):
            cover["presets"] = {
                key: {"form": form, "medium": medium, "style": style, "preamble": preamble}
                for key, (form, medium, style, preamble) in values["PRESETS"].items()
            }
        for field_name, variable in (
            ("shared_rules", "SHARED"),
            ("market_preamble", "MARKET_PREAMBLE"),
            ("market_rules", "SHARED_MARKET"),
        ):
            if isinstance(values.get(variable), str):
                cover[field_name] = values[variable]
        if cover:
            raw["cover"] = cover
    # 寫出去之前先用同一個格式驗過：讀不回來的 brand.yaml 不寫
    Brand.model_validate(raw)
    return raw, name


def build_persona(
    source: SourceTree, persona: str, *, default_genre: str, now: datetime
) -> dict[str, str]:
    """算出角色資料夾裡要寫的每一個檔（相對路徑 → 內容）；不碰硬碟。"""
    if default_genre not in GENRE_NAME:
        raise PersonaImportError(
            f"預設文體 {default_genre} 不在 7 種裡（{'、'.join(GENRE_NAME)}）"
        )
    stamp = now.strftime("%Y-%m-%d")
    brand, name = _brand(source)
    files: dict[str, str] = {
        "voice.md": _voice(source, persona, name, default_genre, stamp),
    }
    for slug in GENRE_NAME:
        text = source.get(f"references/genres/{slug}.md")
        if text is not None:
            files[f"genres/{slug}.md"] = _genre_style(text, persona, slug, stamp)
    for relative, target in _CRAFT.items():
        text = source.get(relative)
        if text is not None:
            files[f"craft/{target}"] = f"<!-- 由 scripts/import_persona.py 匯入（{stamp}） -->\n\n{text}"
    known = _known_themes()
    themes = _themes(source, persona, stamp, known)
    if themes is not None:
        files["themes.md"] = themes
    lessons = _lessons(source, stamp)
    if lessons is not None:
        files["lessons.md"] = lessons
    if brand:
        files["brand.yaml"] = (
            f"# 配圖的品牌設定（ADR-0027）。由 scripts/import_persona.py 匯入（{stamp}）。"
            "格式見 evidence_forge.figures 的說明。\n"
            + yaml.safe_dump(brand, allow_unicode=True, sort_keys=False, width=10_000)
        )
    return dict(sorted(files.items()))


def _known_themes() -> set[str]:
    text = default_harness().themes.read_text(encoding="utf-8")
    return {m.group(1) for line in text.split("\n") if (m := _THEME_ROW.match(line))}


def unused_sources(source: SourceTree) -> list[str]:
    """沒有拆進角色資料夾的來源檔：給人看哪些沒搬（流程說明、範本、其他腳本）。"""
    used = {SKILL, MARKER, _CRAFT_VOICE, _STRATEGY, _LESSONS, _CHART_STYLE, _MAKE_COVER, *_CRAFT}
    used |= {f"references/genres/{slug}.md" for slug in GENRE_NAME}
    return sorted(name for name in source.files if name not in used)


# --- 寫進角色資料夾 -------------------------------------------------------------


def _backup_root(data: DataDir) -> Path:
    """`authors/.backup/`：必須就在 `authors/` 底下，不能是連到別處的符號連結。

    備份會把私人的角色資料整份複製過去；它若被連到公開的 repo 裡，品牌內容就跟著被複製出去。
    """
    root = data.authors / _BACKUP_DIR
    if root.is_symlink() or (root.exists() and root.resolve() != data.authors.resolve() / _BACKUP_DIR):
        raise PersonaImportError(f"{root} 是符號連結或指到別處（{root.resolve()}），不備份也不寫")
    return root


def _backup_dir(data: DataDir, persona: str, now: datetime) -> Path:
    base = _backup_root(data) / f"{persona}-{now.strftime('%Y%m%d-%H%M%S')}"
    candidate = base
    number = 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{number}")
        number += 1
    return candidate


def import_persona(
    data: DataDir, persona: str, files: dict[str, str], *, now: datetime
) -> ImportResult:
    """先檢查每個要寫的位置都在角色資料夾裡，再備份（有東西的話），最後一個檔一個檔換上去。"""
    folder = persona_dir(data, persona)  # 角色名不合格、或資料夾是符號連結就丟錯
    real = folder.resolve()
    for relative in files:
        target = folder / relative
        parent = target.parent
        if parent.exists() and not parent.resolve().is_relative_to(real):
            raise PersonaImportError(
                f"{parent.relative_to(folder)} 連到角色資料夾之外（{parent.resolve()}），不寫"
            )
        if target.is_symlink() and not target.resolve().is_relative_to(real):
            raise PersonaImportError(f"{relative} 是連到角色資料夾之外的符號連結，不寫")
    _backup_root(data)  # 什麼都還沒寫之前就先擋

    backup: Path | None = None
    if folder.is_dir() and any(folder.iterdir()):
        backup = _backup_dir(data, persona, now)
        backup.parent.mkdir(parents=True, exist_ok=True)
        # 符號連結照原樣複製成連結，不跟過去
        shutil.copytree(folder, backup, symlinks=True)

    written: list[str] = []
    notes: list[str] = []
    for relative, content in files.items():
        target = folder / relative
        if relative in _APPEND_ONLY and target.is_file() and target.read_text(encoding="utf-8").strip():
            merged = _append_new_entries(target.read_text(encoding="utf-8"), content)
            if merged is None:
                notes.append(f"{relative} 已經有內容，來源沒有新的條目，沒改")
                continue
            content = merged
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
        written.append(relative)
    return ImportResult(backup=backup, written=written, notes=notes)


def _append_new_entries(existing: str, generated: str) -> str | None:
    """原本的內容一字不動，只把還沒有的條目（`- ` 開頭的行）接在後面；沒有新的就是 None。"""
    have = set(existing.splitlines())
    new = [line for line in generated.splitlines() if line.startswith("- ") and line not in have]
    if not new:
        return None
    return existing + ("" if existing.endswith("\n") else "\n") + "\n".join(new) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="import_persona",
        description="把創作者的寫作系統（zip 或資料夾）拆進 $EF_DATA/authors/<角色>/（ADR-0026 §3.3）",
    )
    parser.add_argument("source", type=Path, help="寫作系統的 zip，或解開後的資料夾")
    parser.add_argument("--persona", default="me", help="角色名（預設 me）")
    parser.add_argument(
        "--default-genre",
        default="explainer",
        help="voice.md 的預設文體；預設 explainer（不需要作者自己經驗的文體裡最常寫的）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只列出會寫哪些檔，不寫")
    args = parser.parse_args(argv)

    now = datetime.now().astimezone()
    try:
        source = read_source(args.source)
        files = build_persona(source, args.persona, default_genre=args.default_genre, now=now)
    except (PersonaImportError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
        print(f"匯入失敗：{exc}", file=sys.stderr)
        return 1
    data = resolve_data_dir()
    target = data.authors / args.persona
    if args.dry_run:
        print(f"會寫進 {target}：")
        for relative in files:
            print(f"  {relative}")
    else:
        try:
            result = import_persona(data, args.persona, files, now=now)
        except (PersonaImportError, ValueError) as exc:
            print(f"匯入失敗：{exc}", file=sys.stderr)
            return 1
        if result.backup is not None:
            print(f"原本的角色資料夾備份在 {result.backup}")
        print(f"寫進 {target}：")
        for relative in result.written:
            print(f"  {relative}")
        for note in result.notes:
            print(f"沒寫：{note}")
    unused = unused_sources(source)
    if unused:
        print("來源裡沒有拆進角色資料夾的檔：")
        for relative in unused:
            print(f"  {relative}")
    return 0
