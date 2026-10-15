"""角色的 lessons：訪談與寫稿開工前讀最近十筆（ADR-0026 §3.3、FR-RW-04、#109）。

`lessons.md` 一筆一行（`- ` 開頭），新的在後；範本裡的說明放在 HTML 註解，不算一筆。
"""

from pathlib import Path

import pytest

from evidence_forge.gateway.scope import ToolRejected
from evidence_forge.lessons import latest_lessons
from evidence_forge.paths import DataDir


def _data(tmp_path: Path) -> DataDir:
    data = DataDir(tmp_path / "data")
    data.ensure()
    (data.authors / "me").mkdir(parents=True)
    return data


@pytest.mark.promise("FR-RW-04")
def test_只回最近十筆_舊的在前(tmp_path: Path) -> None:
    data = _data(tmp_path)
    rows = "\n".join(f"- 2026-10-{n:02d}｜第 {n} 篇｜規則 {n}" for n in range(1, 13))
    (data.authors / "me" / "lessons.md").write_text(
        f"# 退件與指正累積的規則\n\n{rows}\n", encoding="utf-8"
    )

    found = latest_lessons(data, "me")

    assert len(found) == 10
    assert found[0] == "2026-10-03｜第 3 篇｜規則 3"
    assert found[-1] == "2026-10-12｜第 12 篇｜規則 12"


def test_範本的註解與標題不算一筆(tmp_path: Path) -> None:
    data = _data(tmp_path)
    (data.authors / "me" / "lessons.md").write_text(
        "# 規則\n\n<!-- 格式：\n- <日期>｜第 <幾> 篇｜<發生什麼>\n-->\n\n說明文字\n- 圖說限一行\n",
        encoding="utf-8",
    )

    assert latest_lessons(data, "me") == ["圖說限一行"]


def test_還沒有_lessons_md_就是沒有(tmp_path: Path) -> None:
    assert latest_lessons(_data(tmp_path), "me") == []


def test_沒有這個角色_404_列出現有的(tmp_path: Path) -> None:
    with pytest.raises(ToolRejected) as caught:
        latest_lessons(_data(tmp_path), "nobody")
    assert caught.value.status == 404
    assert "me" in caught.value.message


@pytest.mark.parametrize("name", ["../me", "_template", ".hidden", ""])
def test_角色名稱不合格_400_不碰角色資料夾以外(tmp_path: Path, name: str) -> None:
    with pytest.raises(ToolRejected) as caught:
        latest_lessons(_data(tmp_path), name)
    assert caught.value.status in (400, 404)


def test_創作者自己還沒有資料夾_沒有_lessons_就不讀(tmp_path: Path) -> None:
    data = DataDir(tmp_path / "data")
    data.ensure()
    assert latest_lessons(data, "me") == []


def test_lessons_md_是符號連結_403_不讀到角色資料夾以外(tmp_path: Path) -> None:
    data = _data(tmp_path)
    outside = tmp_path / "secret.md"
    outside.write_text("- 不該讀到的東西\n", encoding="utf-8")
    (data.authors / "me" / "lessons.md").symlink_to(outside)

    with pytest.raises(ToolRejected) as caught:
        latest_lessons(data, "me")
    assert caught.value.status == 403


def test_角色資料夾是符號連結_403_不讀(tmp_path: Path) -> None:
    data = _data(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "lessons.md").write_text("- 不該讀到的東西\n", encoding="utf-8")
    (data.authors / "linked").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(ToolRejected) as caught:
        latest_lessons(data, "linked")
    assert caught.value.status == 403
