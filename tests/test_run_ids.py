from __future__ import annotations

from datetime import datetime, timezone

from millrace_engine.run_ids import stable_slug, timestamped_slug_id


def test_stable_slug_normalizes_and_uses_fallback() -> None:
    assert stable_slug("Needs Research", fallback="run") == "needs-research"
    assert stable_slug("   ", fallback="event") == "event"


def test_stable_slug_truncates_with_readable_prefix_and_digest() -> None:
    value = "Promoted task title carrying millrace/millrace_engine/planes/execution.py " * 4

    slug = stable_slug(value, fallback="run", max_length=40)

    assert len(slug) == 40
    assert slug.startswith("promoted-task-title-carryin-")
    assert slug != stable_slug(value, fallback="run")


def test_timestamped_slug_id_preserves_format_and_fallback() -> None:
    moment = datetime(2026, 4, 5, 12, 40, 1, 234567, tzinfo=timezone.utc)

    assert timestamped_slug_id("Resume Needs Research", fallback="run", moment=moment) == (
        "20260405T124001234567Z__resume-needs-research"
    )
    assert timestamped_slug_id("   ", fallback="event", moment=moment) == (
        "20260405T124001234567Z__event"
    )


def test_timestamped_slug_id_applies_optional_slug_length_bound() -> None:
    moment = datetime(2026, 4, 5, 12, 40, 1, 234567, tzinfo=timezone.utc)
    run_id = timestamped_slug_id(
        "Promoted task title carrying millrace/tests/test_execution_plane.py " * 4,
        fallback="run",
        moment=moment,
        max_length=48,
    )

    assert run_id.startswith("20260405T124001234567Z__promoted-task-title-carrying-")
    assert len(run_id.split("__", 1)[1]) == 48
