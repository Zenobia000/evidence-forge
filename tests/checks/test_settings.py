"""檢查的設定檔：預設值跟著程式走，創作者可以在 `$EF_DATA/checks.yaml` 蓋掉其中幾項。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_forge.checks import load_check_settings
from evidence_forge.paths import DataDir

pytestmark = pytest.mark.promise("FR-VF-01")


def test_without_an_override_the_packaged_defaults_apply(tmp_path: Path) -> None:
    settings = load_check_settings(DataDir(tmp_path))
    # Q-28 定案前一律標暫定
    assert settings.c3.provisional is True
    assert 0 < settings.c3.max_ngram_overlap < 1
    assert settings.c3.ngram_size >= 1


def test_override_file_replaces_only_the_fields_it_names(tmp_path: Path) -> None:
    defaults = load_check_settings(DataDir(tmp_path))
    (tmp_path / "checks.yaml").write_text(
        "c3:\n  max_ngram_overlap: 0\n", encoding="utf-8"
    )
    settings = load_check_settings(DataDir(tmp_path))
    assert settings.c3.max_ngram_overlap == 0
    assert settings.c3.ngram_size == defaults.c3.ngram_size
    assert settings.c3.max_common_tokens == defaults.c3.max_common_tokens


def test_typo_in_the_override_file_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "checks.yaml").write_text("c3:\n  max_overlap: 0\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_check_settings(DataDir(tmp_path))
