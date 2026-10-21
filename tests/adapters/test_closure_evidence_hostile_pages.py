"""Hostile-page regression proofs for closure-evidence reads (phase-3 repair 1).

Every page under a materialized session directory is read through the no-follow,
non-blocking, exact-size regular-file gate. These tests replace one page, or the
session directory itself, with each hostile shape and prove that:

* drift refuses the session and names the offending page;
* ``_matches`` refuses the directory so materialization must rebuild;
* a rebuild restores exact authentic bytes, never writes through a link, and
  leaves a symlink target untouched.

Hostile calls run on a daemon thread with a join deadline. A regression that
reintroduces a blocking open (for example on a FIFO) fails the test at the
deadline instead of hanging the suite. Production code carries no watchdog.

Fixture facts: the retained segment pages all have distinct byte lengths, so the
segment substitution swaps in a different-length donor. The same-length
substitution uses the session-2 index page, which is a valid page of another
session with identical length and different bytes.
"""

from __future__ import annotations

import os
import stat
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypeVar

import pytest

from millrace.adapters.cli import closure_evidence
from millrace.adapters.cli.closure_evidence import (
    ClosureEvidenceMaterializationError,
    closure_evidence_drift,
    materialize_session_closure_evidence,
)
from millrace.contracts.closure_request_overflow import ClosureEvidencePage
from tests.adapters import test_closure_evidence_materialization as fixtures

GUARD_SECONDS = 30.0
INDEX_PAGE = "index-00001.txt"
SEGMENT_PAGE = "evidence-0003-00001.txt"
SUBSTITUTED_SEGMENT = "evidence-0002-00001.txt"
DONOR_SEGMENT = "evidence-0004-00001.txt"
TARGETS = (INDEX_PAGE, SEGMENT_PAGE)
TARGET_IDS = ["index-page", "segment-page"]
SHAPES = ("fifo", "directory", "symlink", "oversized", "foreign")
REFUSAL_PHRASE = {
    "fifo": "is not the expected regular file",
    "directory": "is not the expected regular file",
    "symlink": "is not a readable regular file",
    "oversized": "is not the expected regular file",
    "foreign": "changed",
}
ResultT = TypeVar("ResultT")


def _bounded(call: Callable[[], ResultT]) -> ResultT:
    """Run one hostile call under a deadline so a blocking regression fails fast."""
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["value"] = call()
        except BaseException as exc:  # re-raised on the test thread below
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(GUARD_SECONDS)
    assert not worker.is_alive(), (
        "hostile page read did not complete; a blocking open would hang here"
    )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def _page_by_name(
    pages: tuple[ClosureEvidencePage, ...], name: str
) -> ClosureEvidencePage:
    matches = [page for page in pages if Path(page.path).name == name]
    assert len(matches) == 1
    return matches[0]


def _drift(runtime: SimpleNamespace) -> str | None:
    return _bounded(
        lambda: closure_evidence_drift(
            runtime, session=fixtures._session(), dispatch=fixtures._dispatch()
        )
    )


def _matches(directory: Path, pages: tuple[ClosureEvidencePage, ...]) -> bool:
    return bool(_bounded(lambda: closure_evidence._matches(directory, pages)))


def _rebuild(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    pages: tuple[ClosureEvidencePage, ...],
) -> int:
    fixtures._serve(monkeypatch, pages)
    return _bounded(
        lambda: materialize_session_closure_evidence(
            runtime, state=None, session=fixtures._session()
        )
    )


def _materialize_valid(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    pages: tuple[ClosureEvidencePage, ...],
) -> Path:
    fixtures._materialize(
        runtime, monkeypatch, session=fixtures._session(), pages=pages
    )
    return fixtures._directory(runtime)


def _install_hostile(
    shape: str,
    *,
    directory: Path,
    name: str,
    original: bytes,
    scratch: Path,
) -> None:
    """Replace one materialized page with the named hostile shape."""
    fixtures._unlock(directory)
    target = directory / name
    target.unlink()
    if shape == "fifo":
        os.mkfifo(target, 0o600)
    elif shape == "directory":
        target.mkdir()
    elif shape == "symlink":
        elsewhere = scratch / "elsewhere"
        elsewhere.mkdir(exist_ok=True)
        identical = elsewhere / name
        identical.write_bytes(original)
        os.chmod(identical, 0o644)
        target.symlink_to(identical)
    elif shape == "oversized":
        target.write_bytes(original + b"\n" + b"x" * 64)
    elif shape == "foreign":
        target.write_bytes(bytes(byte ^ 0x5A for byte in original))
    else:
        raise AssertionError(f"unknown hostile shape {shape}")


def _swap_bytes(directory: Path, name: str, data: bytes) -> None:
    fixtures._unlock(directory)
    (directory / name).unlink()
    (directory / name).write_bytes(data)


def _write_elsewhere_session(
    scratch: Path, pages: tuple[ClosureEvidencePage, ...]
) -> Path:
    """A real directory holding identical, valid pages outside the workspace."""
    elsewhere = scratch / "elsewhere-session"
    elsewhere.mkdir()
    for page in pages:
        path = elsewhere / Path(page.path).name
        path.write_bytes(page.data)
        os.chmod(path, 0o444)
    os.chmod(elsewhere, 0o555)
    return elsewhere


def _link_session_directory(runtime: SimpleNamespace, target: Path) -> Path:
    link = fixtures._directory(runtime)
    link.parent.mkdir(parents=True)
    link.symlink_to(target, target_is_directory=True)
    return link


@pytest.fixture
def runtime(tmp_path: Path) -> SimpleNamespace:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return SimpleNamespace(
        paths=SimpleNamespace(
            workspace_path=workspace,
            db_path=tmp_path / "runtime" / "millrace.db",
            cas_path=tmp_path / "runtime" / "cas",
        )
    )


@pytest.fixture(autouse=True)
def _restore_writable(tmp_path: Path) -> Iterator[None]:
    yield
    fixtures._make_writable(tmp_path)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_hostile_page_is_refused_by_drift_and_named(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target: str,
    shape: str,
) -> None:
    pages = fixtures._pages()
    directory = _materialize_valid(runtime, monkeypatch, pages)
    assert _drift(runtime) is None
    original = _page_by_name(pages, target).data
    _install_hostile(
        shape, directory=directory, name=target, original=original, scratch=tmp_path
    )

    reason = _drift(runtime)

    assert reason is not None
    assert target in reason
    assert REFUSAL_PHRASE[shape] in reason


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
def test_hostile_page_fails_matches_and_rebuild_restores_authentic_pages(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target: str,
    shape: str,
) -> None:
    pages = fixtures._pages()
    directory = _materialize_valid(runtime, monkeypatch, pages)
    original = _page_by_name(pages, target).data
    _install_hostile(
        shape, directory=directory, name=target, original=original, scratch=tmp_path
    )
    assert not _matches(directory, pages)

    count = _rebuild(runtime, monkeypatch, pages)

    assert count == len(pages)
    assert sorted(os.listdir(directory)) == sorted(
        Path(page.path).name for page in pages
    )
    for page in pages:
        path = directory / Path(page.path).name
        assert stat.S_ISREG(path.lstat().st_mode)
        assert path.read_bytes() == page.data
        assert stat.S_IMODE(path.stat().st_mode) == 0o444
    assert stat.S_IMODE(directory.stat().st_mode) == 0o555
    assert _matches(directory, pages)
    assert _drift(runtime) is None
    if shape == "symlink":
        identical = tmp_path / "elsewhere" / target
        assert identical.read_bytes() == original
        assert stat.S_IMODE(identical.stat().st_mode) == 0o644


def test_segment_page_swapped_for_another_valid_page_is_refused_and_rebuilt(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = fixtures._pages()
    directory = _materialize_valid(runtime, monkeypatch, pages)
    original = _page_by_name(pages, SUBSTITUTED_SEGMENT).data
    donor = _page_by_name(pages, DONOR_SEGMENT).data
    assert donor != original
    assert len(donor) != len(original)
    _swap_bytes(directory, SUBSTITUTED_SEGMENT, donor)

    reason = _drift(runtime)

    assert reason is not None
    assert SUBSTITUTED_SEGMENT in reason
    assert REFUSAL_PHRASE["oversized"] in reason
    assert not _matches(directory, pages)

    assert _rebuild(runtime, monkeypatch, pages) == len(pages)
    assert (directory / SUBSTITUTED_SEGMENT).read_bytes() == original
    assert _drift(runtime) is None


def test_index_page_swapped_for_another_sessions_valid_index_is_refused_and_rebuilt(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = fixtures._pages()
    donor_pages = fixtures._pages(session_id="session-2")
    directory = _materialize_valid(runtime, monkeypatch, pages)
    original = _page_by_name(pages, INDEX_PAGE).data
    donor = _page_by_name(donor_pages, INDEX_PAGE).data
    assert len(donor) == len(original)
    assert donor != original
    _swap_bytes(directory, INDEX_PAGE, donor)

    reason = _drift(runtime)

    assert reason is not None
    assert INDEX_PAGE in reason
    assert REFUSAL_PHRASE["foreign"] in reason
    assert not _matches(directory, pages)

    assert _rebuild(runtime, monkeypatch, pages) == len(pages)
    assert (directory / INDEX_PAGE).read_bytes() == original
    assert _drift(runtime) is None


def test_symlinked_session_directory_is_refused_by_matches_and_drift(
    runtime: SimpleNamespace, tmp_path: Path
) -> None:
    pages = fixtures._pages()
    elsewhere = _write_elsewhere_session(tmp_path, pages)
    link = _link_session_directory(runtime, elsewhere)

    assert link.is_symlink()
    assert not _matches(link, pages)
    reason = _drift(runtime)
    assert reason is not None
    assert "symlink" in reason


def test_symlinked_session_directory_is_never_written_through(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pages = fixtures._pages()
    elsewhere = _write_elsewhere_session(tmp_path, pages)
    link = _link_session_directory(runtime, elsewhere)
    before = {name: (elsewhere / name).read_bytes() for name in os.listdir(elsewhere)}
    fixtures._serve(monkeypatch, pages)

    with pytest.raises(ClosureEvidenceMaterializationError, match="symlink"):
        _bounded(
            lambda: materialize_session_closure_evidence(
                runtime, state=None, session=fixtures._session()
            )
        )

    assert link.is_symlink()
    assert sorted(os.listdir(elsewhere)) == sorted(before)
    for name, data in before.items():
        assert (elsewhere / name).read_bytes() == data
    assert stat.S_IMODE(elsewhere.stat().st_mode) == 0o555
