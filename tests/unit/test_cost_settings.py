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


def test_設定檔是極深的巢狀_JSON_也不當掉(data: DataDir) -> None:
    # 閘道每次預留都讀設定：這裡丟例外，每個模型請求都會變成 500
    _write(data, "[" * 100_000 + "]" * 100_000)

    loaded = cost_settings.load(data)

    assert loaded.settings == CostSettings()
    assert loaded.problem is not None


def test_改設定是讀_改_寫一起做_同時改不同欄位不會互相蓋掉(data: DataDir) -> None:
    import threading

    names = ("analysis_budget_usd", "rewrite_budget_usd", "image_cap_usd", "monthly_cap_usd")
    workers = [
        threading.Thread(
            target=cost_settings.update,
            args=(data, lambda current, name=name: current.model_copy(update={name: 2.0})),
            kwargs={"now": NOW},
        )
        for name in names
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert cost_settings.load(data).settings == CostSettings(**{name: 2.0 for name in names})


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


# --- 審查第 1 輪（T3）：金額上限、壞編碼、壞檔沿用最後存的值、先換檔再記、預算不能是 0 ----------------


@pytest.mark.promise("FR-LG-03")
@pytest.mark.parametrize(
    "text",
    [
        '{"monthly_cap_usd": 1e25}',
        '{"analysis_budget_usd": 1e308}',
        '{"image_cap_usd": 1000000.01}',
        '{"analysis_budget_usd": 0}',
        '{"rewrite_budget_usd": 0.0}',
        '{"image_cap_usd": 0}',
    ],
    ids=["上限太大", "預算太大", "生圖上限剛好超過", "分析預算是0", "寫稿預算是0", "生圖上限是0"],
)
def test_金額超過一百萬或預算是_0_都算讀不懂(data: DataDir, text: str) -> None:
    # 太大的數字換成微美元時會丟例外，每個付費請求都會 500；預算是 0 的單一開就失敗
    _write(data, text)

    loaded = cost_settings.load(data)

    assert loaded.settings == CostSettings()
    assert loaded.problem is not None


def test_每月上限可以是_0_也可以剛好一百萬(data: DataDir) -> None:
    _write(data, '{"monthly_cap_usd": 0, "analysis_budget_usd": 1000000}')

    loaded = cost_settings.load(data)

    assert loaded.problem is None
    assert (loaded.settings.monthly_cap_usd, loaded.settings.analysis_budget_usd) == (0.0, 1_000_000.0)


def test_設定檔不是_UTF_8_算讀不懂_不當掉(data: DataDir) -> None:
    path = data.root / "settings" / "costs.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"monthly_cap_usd": \xff\xfe}')

    loaded = cost_settings.load(data)

    assert loaded.settings == CostSettings()
    assert loaded.problem is not None


@pytest.mark.parametrize("value", ["1e25", "1000000.5", "1e400"])
def test_環境變數的每月上限超過一百萬就不啟動(value: str) -> None:
    with pytest.raises(ValueError, match="EF_MONTHLY_BUDGET_USD"):
        cost_settings.env_monthly_cap({"EF_MONTHLY_BUDGET_USD": value})


@pytest.mark.promise("FR-LG-03")
def test_設定檔壞掉_沿用改動紀錄裡每個欄位最後存的值_沒記過的用預設值(data: DataDir) -> None:
    cost_settings.save(data, CostSettings(monthly_cap_usd=10.0, rewrite_budget_usd=0.9), now=NOW)
    cost_settings.save(data, CostSettings(monthly_cap_usd=12.0, rewrite_budget_usd=0.9), now=NOW)
    # 紀錄裡壞掉的一行跳過
    with (data.root / "settings" / "changes.jsonl").open("a", encoding="utf-8") as log:
        log.write("{半行\n")
    _write(data, "{壞掉")

    loaded = cost_settings.load(data)

    assert loaded.settings == CostSettings(monthly_cap_usd=12.0, rewrite_budget_usd=0.9)
    assert loaded.problem is not None and "最後一次存的值" in loaded.problem


def test_設定檔壞掉_改動紀錄裡的值不合法就那一欄用預設值(data: DataDir) -> None:
    path = data.root / "settings" / "changes.jsonl"
    path.parent.mkdir(parents=True)
    lines = [
        {"ts": "2026-10-15T09:30:00+08:00", "field": "monthly_cap_usd", "old": None, "new": 8.0},
        {"ts": "2026-10-15T09:30:00+08:00", "field": "analysis_budget_usd", "old": 0.5, "new": -3},
    ]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    _write(data, "{壞掉")

    assert cost_settings.load(data).settings == CostSettings(monthly_cap_usd=8.0)


def test_設定檔壞掉時存檔_舊值記成當時實際在用的那一份_一樣的值不記(data: DataDir) -> None:
    cost_settings.save(data, CostSettings(monthly_cap_usd=10.0), now=NOW)
    _write(data, "{壞掉")
    later = datetime(2026, 10, 16, 8, 0, tzinfo=ZoneInfo("Asia/Taipei"))

    cost_settings.save(data, CostSettings(monthly_cap_usd=10.0, image_cap_usd=0.3), now=later)

    assert cost_settings.load(data).problem is None
    assert _changes(data)[1:] == [
        {"ts": "2026-10-16T08:00:00+08:00", "field": "image_cap_usd", "old": 0.25, "new": 0.3}
    ]


def test_換檔失敗就不記改動(data: DataDir, monkeypatch: pytest.MonkeyPatch) -> None:
    def disk_full(*_: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("evidence_forge.cost_settings.os.replace", disk_full)

    with pytest.raises(OSError):
        cost_settings.save(data, CostSettings(monthly_cap_usd=3.0), now=NOW)
    assert not (data.root / "settings" / "changes.jsonl").exists()
    assert cost_settings.load(data).settings == CostSettings()


# --- codex 第 2 輪：改動紀錄的每一行先驗形狀、沿用最近一筆合法的值、紀錄寫不進去不算存檔失敗 --------------


def _log_lines(data: DataDir, rows: list[object]) -> None:
    path = data.root / "settings" / "changes.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.mark.promise("FR-LG-03")
def test_改動紀錄有形狀不對的行_跳過_不讓閘道讀設定時當掉(data: DataDir) -> None:
    _log_lines(
        data,
        [
            {"field": "monthly_cap_usd", "new": 7.0},
            {"field": ["monthly_cap_usd"], "new": 1.0},
            {"field": {"a": 1}, "new": 1.0},
            {"field": 3, "new": 1.0},
            {"field": None, "new": 1.0},
            ["monthly_cap_usd", 1.0],
            "monthly_cap_usd",
            42,
        ],
    )
    _write(data, "{壞掉")

    loaded = cost_settings.load(data)

    assert loaded.settings == CostSettings(monthly_cap_usd=7.0)
    assert loaded.problem is not None


@pytest.mark.promise("FR-LG-03")
def test_最後一筆不合法就往前找最近一筆合法的(data: DataDir) -> None:
    _log_lines(
        data,
        [
            {"field": "monthly_cap_usd", "old": None, "new": 10.0},
            {"field": "monthly_cap_usd", "old": 10.0, "new": 12.0},
            {"field": "monthly_cap_usd", "old": 12.0, "new": -5},
            {"field": "monthly_cap_usd", "old": -5, "new": "很多"},
            {"field": "analysis_budget_usd", "old": 0.5, "new": 0.7},
            {"field": "analysis_budget_usd", "old": 0.7, "new": 0},
        ],
    )
    _write(data, "{壞掉")

    assert cost_settings.load(data).settings == CostSettings(monthly_cap_usd=12.0, analysis_budget_usd=0.7)


def test_最後一筆合法的是不設_就是不設(data: DataDir) -> None:
    _log_lines(
        data,
        [
            {"field": "monthly_cap_usd", "old": None, "new": 10.0},
            {"field": "monthly_cap_usd", "old": 10.0, "new": None},
        ],
    )
    _write(data, "{壞掉")

    assert cost_settings.load(data).settings.monthly_cap_usd is None


@pytest.mark.promise("FR-LG-03")
def test_換好檔但改動紀錄寫不進去_設定照樣生效_回報紀錄沒寫成(data: DataDir) -> None:
    # 紀錄的位置被一個資料夾佔住：附加寫入一定失敗
    (data.root / "settings" / "changes.jsonl").mkdir(parents=True)

    saved = cost_settings.save(data, CostSettings(monthly_cap_usd=4.0), now=NOW)

    assert cost_settings.load(data).settings == CostSettings(monthly_cap_usd=4.0)
    assert [(c["field"], c["new"]) for c in saved.changes] == [("monthly_cap_usd", 4.0)]
    assert saved.log_error is not None


def test_改動紀錄寫得進去時_沒有錯誤(data: DataDir) -> None:
    saved = cost_settings.save(data, CostSettings(monthly_cap_usd=4.0), now=NOW)

    assert saved.log_error is None
    assert len(_changes(data)) == 1
