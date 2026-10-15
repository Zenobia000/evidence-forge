"""照編輯卡寫的文章，執行器從工單快照讀出來的檢查規則（`hard_checks.article_rules`，#110）。

文章裡准出現的網址只有這幾種來源：原文、分析放進來的來源、編輯卡摘要（`card.md`，只有現在的決定）、
作者素材、創作者這次改稿的意見。決策紀錄（`decisions.jsonl`，含被換掉的提案與 agent 寫的選項）不算。
"""

from pathlib import Path

from evidence_forge.cards import CardState
from evidence_forge.checks.text import normalize_url
from evidence_forge.runner.hard_checks import article_rules
from evidence_forge.runner.sources import Original

CARD = "https://card.example/decided"
MATERIAL = "https://notes.example/mine"
OLD_PROPOSAL = "https://agent.example/superseded"
SOURCE = "https://source.example/s2"
ORIGINAL = "https://seed.example/post"
FEEDBACK = "https://creator.example/new-link"


def _job(tmp_path: Path, *, sources: bool = True) -> Path:
    job_dir = tmp_path / "job"
    card = job_dir / "input" / "card"
    (card / "materials").mkdir(parents=True)
    state = CardState(card_id="card-1", conversation_id="c-1", genre="research-review", theme="education", stations=[])
    # 快取的 card.json 與決策紀錄裡寫了別的網址：都不算
    (card / "card.json").write_text(state.model_dump_json().replace('"card-1"', f'"card-1 {OLD_PROPOSAL}"'), encoding="utf-8")
    (card / "card.md").write_text(f"# 編輯卡\n\n- 創作者自己寫：{CARD}\n", encoding="utf-8")
    (card / "decisions.jsonl").write_text(f'{{"description": "{OLD_PROPOSAL}"}}\n', encoding="utf-8")
    (card / "materials" / ("a" * 64)).write_text(f"我的筆記 {MATERIAL}", encoding="utf-8")
    if sources:
        placed = job_dir / "input" / "sources"
        placed.mkdir(parents=True)
        (placed / "S2.md").write_text(f"來源編號：S2\n標題：來源\n網址：{SOURCE}\n\n---\n\n正文\n", encoding="utf-8")
    return job_dir


def test_准出現的網址_編輯卡摘要_作者素材_來源_原文_改稿意見(tmp_path: Path) -> None:
    rules = article_rules(
        _job(tmp_path), Original(title="原文", text="原文", url=ORIGINAL), feedback=f"加上 {FEEDBACK} 這個連結"
    )

    assert rules is not None and rules.known_urls is not None
    for url in (CARD, MATERIAL, SOURCE, ORIGINAL, FEEDBACK):
        assert normalize_url(url) in rules.known_urls, url
    assert normalize_url(OLD_PROPOSAL) not in rules.known_urls
    assert rules.list_required is True


def test_沒有原文也沒有來源_參考來源可以不列清單(tmp_path: Path) -> None:
    rules = article_rules(_job(tmp_path, sources=False), None)

    assert rules is not None and rules.list_required is False


def test_沒有原文但有來源_參考來源要列清單(tmp_path: Path) -> None:
    rules = article_rules(_job(tmp_path), None)

    assert rules is not None and rules.list_required is True


def test_作者素材放的位置跟編輯卡快照同一個() -> None:
    from evidence_forge import cards
    from evidence_forge.runner.hard_checks import CARD_MATERIALS

    assert CARD_MATERIALS == cards.SNAPSHOT_MATERIALS
