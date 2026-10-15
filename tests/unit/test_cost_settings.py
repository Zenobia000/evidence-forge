"""費用設定（`$EF_DATA/settings/costs.json`，FR-LG-03、ADR-0027 §3.6）：讀、寫、改動紀錄、環境變數蓋過。"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from evidence_forge import cost_settings
from evidence_forge.cost_settings import CostSettings
from evidence_forge.paths import DataDir

NOW = datetime(2026, 10, 15, 9, 30, tzinfo=ZoneInfo("Asia/Taipei"))


@pytest.fixture
def data(tmp_path: Path) -> DataDir:
    return DataDir(tmp_path / "data")


@pytest.mark.promise("FR-LG-03")
def test_沒有設定檔_不設每月上限_單張預算照現在的預設(data: DataDir) -> None:
    loaded = cost_settings.load(data)

    assert loaded.problem is None
    assert loaded.settings.monthly_cap_usd is None
    assert (loaded.settings.analysis_budget_usd, loaded.settings.rewrite_budget_usd) == (0.5, 0.5)


def _write(data: DataDir, text: str) -> None:
    path = data.root / "settings" / "costs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_設定檔讀得懂_照檔案的值_整數金額也收_沒寫的欄位用預設值(data: DataDir) -> None:
    _write(data, '{"monthly_cap_usd": 20, "rewrite_budget_usd": 1.25}')

    loaded = cost_settings.load(data)

    assert loaded.problem is None
    assert loaded.settings == CostSettings(monthly_cap_usd=20.0, rewrite_budget_usd=1.25)
    assert loaded.settings.analysis_budget_usd == 0.5


@pytest.mark.promise("FR-LG-03")
@pytest.mark.parametrize(
    "text",
    [
        "{不是 JSON",
        "[]",
        '{"monthly_cap_usd": -1}',
        '{"monthly_cap_usd": "10"}',
        '{"analysis_budget_usd": true}',
        '{"image_cap_usd": Infinity}',
        '{"rewrite_budget_usd": NaN}',
    ],
)
def test_設定檔讀不懂_用預設值_說得出原因_不當掉(data: DataDir, text: str) -> None:
    _write(data, text)

    loaded = cost_settings.load(data)

    assert loaded.settings == CostSettings()
    assert loaded.problem is not None and "costs.json" in loaded.problem


def _changes(data: DataDir) -> list[dict[str, object]]:
    path = data.root / "settings" / "changes.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.promise("FR-LG-03")
def test_存設定_讀回來一樣_每個改到的欄位記一筆_時間舊值新值(data: DataDir) -> None:
    cost_settings.save(data, CostSettings(monthly_cap_usd=10.0, analysis_budget_usd=0.8), now=NOW)
    later = datetime(2026, 10, 16, 8, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    cost_settings.save(data, CostSettings(monthly_cap_usd=None, analysis_budget_usd=0.8), now=later)

    assert cost_settings.load(data) == cost_settings.Loaded(CostSettings(analysis_budget_usd=0.8))
    assert _changes(data) == [
        {"ts": "2026-10-15T09:30:00+08:00", "field": "monthly_cap_usd", "old": None, "new": 10.0},
        {"ts": "2026-10-15T09:30:00+08:00", "field": "analysis_budget_usd", "old": 0.5, "new": 0.8},
        {"ts": "2026-10-16T08:00:00+08:00", "field": "monthly_cap_usd", "old": 10.0, "new": None},
    ]


def test_存一樣的值不記改動(data: DataDir) -> None:
    cost_settings.save(data, CostSettings(monthly_cap_usd=3.0), now=NOW)
    cost_settings.save(data, CostSettings(monthly_cap_usd=3.0), now=NOW)

    assert len(_changes(data)) == 1


def test_壞掉的設定檔存一次就修好_舊值記成當時實際用的預設值(data: DataDir) -> None:
    _write(data, "{壞掉")

    cost_settings.save(data, CostSettings(monthly_cap_usd=5.0), now=NOW)

    assert cost_settings.load(data).problem is None
    assert _changes(data) == [
        {"ts": "2026-10-15T09:30:00+08:00", "field": "monthly_cap_usd", "old": None, "new": 5.0}
    ]
