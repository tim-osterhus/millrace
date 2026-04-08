from __future__ import annotations

from millrace_engine.markdown import write_text_atomic


def test_write_text_atomic_supports_long_target_filenames(tmp_path) -> None:
    target = tmp_path / (("long-filename-" * 16) + ".json")

    write_text_atomic(target, "payload\n")

    assert target.read_text(encoding="utf-8") == "payload\n"
