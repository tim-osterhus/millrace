from __future__ import annotations

from pathlib import Path
import json

from tests.support import fixture_source
from tests.support_shadow_mode import build_shadow_mode_equivalence_report


def test_shadow_mode_equivalence_matches_expected_comparison_output(tmp_path: Path) -> None:
    actual = build_shadow_mode_equivalence_report(tmp_path)
    expected = json.loads(
        (fixture_source("parity/shadow_mode_equivalence") / "expected_comparison.json").read_text(
            encoding="utf-8"
        )
    )

    assert actual == expected
