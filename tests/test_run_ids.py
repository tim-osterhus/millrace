from __future__ import annotations

from datetime import datetime, timezone

from millrace_engine.run_ids import stable_slug, timestamped_slug_id


def test_stable_slug_normalizes_and_uses_fallback() -> None:
    assert stable_slug("Needs Research", fallback="run") == "needs-research"
    assert stable_slug("   ", fallback="event") == "event"


def test_timestamped_slug_id_preserves_format_and_fallback() -> None:
    moment = datetime(2026, 4, 5, 12, 40, 1, 234567, tzinfo=timezone.utc)

    assert timestamped_slug_id("Resume Needs Research", fallback="run", moment=moment) == (
        "20260405T124001234567Z__resume-needs-research"
    )
    assert timestamped_slug_id("   ", fallback="event", moment=moment) == (
        "20260405T124001234567Z__event"
    )
