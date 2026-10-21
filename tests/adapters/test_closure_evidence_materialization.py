"""Session-owned closure evidence: materialization, drift, cleanup, diagnostics.

The tests drive the real `millrace.adapters.cli.closure_evidence` functions with
a minimal runtime namespace. Page bytes come from the real overflow projection of
the retained C25 closure request fixture; only the durable page lookup
(`closure_evidence_pages_for_run`) is replaced. Everything lives under `tmp_path`.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from millrace.adapters.cli import closure_evidence, session_persistence
from millrace.adapters.cli.closure_evidence import (
    ClosureEvidenceMaterializationError,
    cleanup_session_closure_evidence,
    closure_evidence_drift,
    materialize_session_closure_evidence,
    record_materialization_refusal,
)
from millrace.adapters.runner_contract import RedactionPolicy
from millrace.contracts.closure_request_overflow import (
    ClosureEvidencePage,
    closure_request_dispatch_payload,
    closure_request_evidence_pages,
    materialization_component,
)
from millrace.contracts.compiled_plan import (
    ClosureRequestOverflowDeclaration,
    CompletionBehaviorDeclaration,
    CompletionBehaviorWithRequestOverflowDeclaration,
)
from millrace.contracts.ids import (
    ActionId,
    ArtifactSchemaId,
    CompletionBehaviorId,
    QueueFamilyId,
    RemediationPolicyId,
    RunnerBindingId,
    StageKindId,
)
from millrace.contracts.transition import canonical_authority_mapping_bytes
from tests.adapters.test_codex_adapter import _valid_dispatch_envelope

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "closure_overflow"
    / "c25_retained_closure_request_payload.json"
)
ROOT = "closure-evidence"
COMPONENT = materialization_component("session-1", 1)
POLICY = RedactionPolicy("redact", ("secret-value",))
INLINE_LIMIT = 8192

def _base_behavior() -> CompletionBehaviorDeclaration:
    return CompletionBehaviorDeclaration(
        id=CompletionBehaviorId("completion.c25"),
        trigger="backlog_drained",
        readiness_rule="no_open_lineage_work",
        request_kind="closure_target",
        target_selector="active_closure_target",
        target_stage_kind_id=StageKindId("review"),
        target_graph_node_id="review.start",
        runner_binding_id=RunnerBindingId("review.runner"),
        request_queue_family_id=QueueFamilyId("review"),
        pass_action_id=ActionId("review.pass"),
        gap_action_id=ActionId("review.gap"),
        blocked_action_id=ActionId("review.blocked"),
        verdict_artifact_schema_id=ArtifactSchemaId("review.verdict"),
        evidence_artifact_schema_ids=(ArtifactSchemaId("review.evidence"),),
        evidence_item_limit=96,
        request_payload_byte_limit=INLINE_LIMIT,
        remediation_policy_id=RemediationPolicyId("review.remediation"),
        accepted_root_source_kinds=("origin",),
        root_source_resolution="runtime_inventory",
        evidence_window_policy="lineage",
        rubric_policy="reuse_or_create",
        blocked_work_policy="suppress",
        skip_if_closed=True,
        presentation={},
    )


def _overflow_behavior(
    root: str = ROOT,
) -> CompletionBehaviorWithRequestOverflowDeclaration:
    return CompletionBehaviorWithRequestOverflowDeclaration.from_behavior(
        _base_behavior(),
        request_overflow=ClosureRequestOverflowDeclaration(
            policy="materialized_evidence_pages",
            materialization_root=root,
            page_byte_limit=4096,
            max_snapshot_bytes=1_048_576,
            max_page_count=1024,
        ),
    )


def _retained_payload() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _pages(
    *,
    session_id: str = "session-1",
    generation: int = 1,
    root: str = ROOT,
) -> tuple[ClosureEvidencePage, ...]:
    return closure_request_evidence_pages(
        _retained_payload(),
        behavior=_overflow_behavior(root),
        session_id=session_id,
        dispatch_generation=generation,
    )


def _dispatch(
    *,
    session_id: str = "session-1",
    generation: int = 1,
    payload: dict[str, Any] | None = None,
) -> Any:
    projected = closure_request_dispatch_payload(
        _retained_payload() if payload is None else payload,
        behavior=_overflow_behavior(),
        session_id=session_id,
        dispatch_generation=generation,
    )
    return _valid_dispatch_envelope(
        session_id=session_id,
        dispatch_generation=generation,
        work_item_payload=projected,
    )


def _session(session_id: str = "session-1", generation: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=session_id,
        run_id="run-1",
        dispatch_generation=generation,
        session_fencing_token=f"fence-{session_id}-{generation}",
    )


def _workspace(runtime: SimpleNamespace) -> Path:
    return Path(runtime.paths.workspace_path)


def _directory(
    runtime: SimpleNamespace, *, root: str = ROOT, component: str = COMPONENT
) -> Path:
    return _workspace(runtime) / root / component


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    pages: tuple[ClosureEvidencePage, ...],
) -> None:
    monkeypatch.setattr(
        closure_evidence,
        "closure_evidence_pages_for_run",
        lambda *, state, run_id: pages,
    )


def _materialize(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    *,
    session: SimpleNamespace,
    pages: tuple[ClosureEvidencePage, ...],
) -> int:
    _serve(monkeypatch, pages)
    return materialize_session_closure_evidence(runtime, state=None, session=session)


def _segment_names(pages: tuple[ClosureEvidencePage, ...]) -> list[str]:
    return [
        Path(page.path).name
        for page in pages
        if not Path(page.path).name.startswith("index-")
    ]


def _unlock(directory: Path) -> None:
    os.chmod(directory, 0o700)
    for entry in directory.iterdir():
        os.chmod(entry, 0o644)


def _mutate_changed(directory: Path, pages: tuple[ClosureEvidencePage, ...]) -> None:
    _unlock(directory)
    target = directory / Path(pages[-1].path).name
    target.write_bytes(b"tampered page bytes")


def _mutate_extra(directory: Path, pages: tuple[ClosureEvidencePage, ...]) -> None:
    _unlock(directory)
    (directory / "extra.txt").write_bytes(b"extra")


def _mutate_missing(directory: Path, pages: tuple[ClosureEvidencePage, ...]) -> None:
    _unlock(directory)
    (directory / Path(pages[-1].path).name).unlink()


def _make_writable(root: Path) -> None:
    """Restore write access so tmp_path teardown can remove read-only pages."""
    for current, _dirs, files in os.walk(root):
        os.chmod(current, 0o700)
        for name in files:
            path = os.path.join(current, name)
            if not os.path.islink(path):
                os.chmod(path, 0o600)


class _FailingOs:
    """Stands in for the module's `os` name; only the named call fails."""

    def __init__(self, failing: str) -> None:
        self._failing = failing

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    def rename(self, src: Any, dst: Any) -> None:
        if self._failing == "rename":
            raise OSError("injected rename failure")
        os.rename(src, dst)

    def fsync(self, fd: int) -> None:
        if self._failing == "fsync":
            raise OSError("injected fsync failure")
        os.fsync(fd)


@pytest.fixture(autouse=True)
def _restore_writable(tmp_path: Path) -> Iterator[None]:
    yield
    _make_writable(tmp_path)


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


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    recorded: list[dict[str, Any]] = []

    def record(runtime: object, **kwargs: Any) -> None:
        recorded.append(kwargs)

    monkeypatch.setattr(session_persistence, "_record_session_event", record)
    return recorded


def test_fixture_body_exceeds_the_inline_limit_it_is_used_with() -> None:
    canonical = canonical_authority_mapping_bytes(_retained_payload())
    assert len(canonical) == 18566
    assert len(canonical) > INLINE_LIMIT
    assert len(_pages()) > 2


def test_first_write_materializes_exact_bytes_under_read_only_modes(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = _pages()
    count = _materialize(runtime, monkeypatch, session=_session(), pages=pages)

    assert count == len(pages)
    directory = _directory(runtime)
    assert all(page.path.startswith(f"{ROOT}/{COMPONENT}/") for page in pages)
    assert sorted(os.listdir(directory)) == sorted(Path(p.path).name for p in pages)
    assert "index-00001.txt" in os.listdir(directory)
    for page in pages:
        path = _workspace(runtime) / page.path
        assert path.read_bytes() == page.data
        assert stat.S_IMODE(path.stat().st_mode) == 0o444
    assert stat.S_IMODE(directory.stat().st_mode) == 0o555
    assert os.listdir(_workspace(runtime) / ROOT) == [COMPONENT]


def test_empty_page_set_is_a_no_op(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    count = _materialize(runtime, monkeypatch, session=_session(), pages=())

    assert count == 0
    assert not (_workspace(runtime) / ROOT).exists()


def test_matching_directory_is_reused_without_rewrite(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = _pages()
    _materialize(runtime, monkeypatch, session=_session(), pages=pages)
    directory = _directory(runtime)
    names = [directory, *(directory / Path(p.path).name for p in pages)]
    before = {str(path): os.stat(path).st_ino for path in names}

    count = _materialize(runtime, monkeypatch, session=_session(), pages=pages)

    assert count == len(pages)
    assert {str(path): os.stat(path).st_ino for path in names} == before


@pytest.mark.parametrize(
    "mutate",
    [_mutate_changed, _mutate_extra, _mutate_missing],
    ids=["changed-page", "extra-file", "missing-page"],
)
def test_rebuilds_session_directory_when_pages_no_longer_match(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[Path, tuple[ClosureEvidencePage, ...]], None],
) -> None:
    pages = _pages()
    _materialize(runtime, monkeypatch, session=_session(), pages=pages)
    mutate(_directory(runtime), pages)

    count = _materialize(runtime, monkeypatch, session=_session(), pages=pages)

    assert count == len(pages)
    directory = _directory(runtime)
    assert sorted(os.listdir(directory)) == sorted(Path(p.path).name for p in pages)
    for page in pages:
        path = _workspace(runtime) / page.path
        assert path.read_bytes() == page.data
        assert stat.S_IMODE(path.stat().st_mode) == 0o444
    assert stat.S_IMODE(directory.stat().st_mode) == 0o555
    assert os.listdir(_workspace(runtime) / ROOT) == [COMPONENT]


@pytest.mark.parametrize(
    ("root", "link"),
    [
        ("closure-evidence", "closure-evidence"),
        ("outer/closure-evidence", "outer"),
        ("outer/closure-evidence", "outer/closure-evidence"),
    ],
    ids=["leaf-symlink", "intermediate-symlink", "nested-leaf-symlink"],
)
def test_symlinked_materialization_path_is_refused_without_writing_through(
    tmp_path: Path,
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    root: str,
    link: str,
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link_path = _workspace(runtime) / link
    link_path.parent.mkdir(parents=True, exist_ok=True)
    link_path.symlink_to(elsewhere, target_is_directory=True)
    pages = _pages(root=root)

    with pytest.raises(ClosureEvidenceMaterializationError, match="symlink"):
        _materialize(runtime, monkeypatch, session=_session(), pages=pages)

    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize(
    ("field", "relative"),
    [
        ("db_path", f"{ROOT}/{COMPONENT}/millrace.db"),
        ("cas_path", f"{ROOT}/{COMPONENT}/cas"),
        ("db_path", f"{ROOT}/{COMPONENT}"),
        ("cas_path", ROOT),
        ("db_path", ""),
    ],
    ids=[
        "db-inside",
        "cas-inside",
        "db-is-directory",
        "cas-is-root",
        "db-is-workspace",
    ],
)
def test_overlap_with_protected_runtime_paths_is_refused(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    relative: str,
) -> None:
    setattr(runtime.paths, field, _workspace(runtime) / relative)

    with pytest.raises(
        ClosureEvidenceMaterializationError,
        match="overlaps a protected runtime path",
    ):
        _materialize(runtime, monkeypatch, session=_session(), pages=_pages())

    assert not (_workspace(runtime) / ROOT).exists()


@pytest.mark.parametrize(
    ("page_session", "page_generation"),
    [("other-session", 1), ("session-1", 2)],
    ids=["foreign-session", "foreign-generation"],
)
def test_pages_not_owned_by_the_session_are_refused(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    page_session: str,
    page_generation: int,
) -> None:
    pages = _pages(session_id=page_session, generation=page_generation)

    with pytest.raises(ClosureEvidenceMaterializationError, match="not session-owned"):
        _materialize(runtime, monkeypatch, session=_session(), pages=pages)

    assert not (_workspace(runtime) / ROOT).exists()


def test_pages_spanning_two_directories_are_refused(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = (
        ClosureEvidencePage(f"{ROOT}/{COMPONENT}/index-00001.txt", b"{}"),
        ClosureEvidencePage(f"{ROOT}/other/extra.txt", b"x"),
    )

    with pytest.raises(
        ClosureEvidenceMaterializationError, match="share one directory"
    ):
        _materialize(runtime, monkeypatch, session=_session(), pages=pages)


@pytest.mark.parametrize("failing", ["rename", "fsync"])
def test_temporary_directory_is_removed_when_a_write_fails(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    failing: str,
) -> None:
    monkeypatch.setattr(closure_evidence, "os", _FailingOs(failing))
    pages = _pages()

    with pytest.raises(OSError, match="injected"):
        _materialize(runtime, monkeypatch, session=_session(), pages=pages)

    assert not _directory(runtime).exists()
    assert os.listdir(_workspace(runtime) / ROOT) == []


def test_intact_pages_have_no_drift(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _materialize(runtime, monkeypatch, session=_session(), pages=_pages())

    assert (
        closure_evidence_drift(runtime, session=_session(), dispatch=_dispatch())
        is None
    )


def test_changed_segment_page_is_reported_by_name(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = _pages()
    _materialize(runtime, monkeypatch, session=_session(), pages=pages)
    name = _segment_names(pages)[-1]
    _unlock(_directory(runtime))
    target = _directory(runtime) / name
    data = target.read_bytes()
    target.write_bytes(data[:-1] + bytes([data[-1] ^ 0x01]))

    reason = closure_evidence_drift(runtime, session=_session(), dispatch=_dispatch())

    assert reason is not None
    assert name in reason
    assert "changed" in reason


def test_removed_segment_page_is_reported(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = _pages()
    _materialize(runtime, monkeypatch, session=_session(), pages=pages)
    _unlock(_directory(runtime))
    (_directory(runtime) / _segment_names(pages)[0]).unlink()

    reason = closure_evidence_drift(runtime, session=_session(), dispatch=_dispatch())

    assert reason is not None
    assert "added or removed" in reason


def test_added_file_is_reported(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _materialize(runtime, monkeypatch, session=_session(), pages=_pages())
    _unlock(_directory(runtime))
    (_directory(runtime) / "injected.txt").write_bytes(b"not authentic")

    reason = closure_evidence_drift(runtime, session=_session(), dispatch=_dispatch())

    assert reason is not None
    assert "added or removed" in reason


def test_corrupted_index_page_is_reported_as_unverifiable(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _materialize(runtime, monkeypatch, session=_session(), pages=_pages())
    _unlock(_directory(runtime))
    (_directory(runtime) / "index-00001.txt").write_bytes(b"corrupt index")

    reason = closure_evidence_drift(runtime, session=_session(), dispatch=_dispatch())

    assert reason is not None
    assert reason.startswith("closure evidence pages could not be verified")
    assert "index-00001.txt" in reason


def test_removed_directory_is_reported_as_unverifiable(
    runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _materialize(runtime, monkeypatch, session=_session(), pages=_pages())
    _unlock(_directory(runtime))
    for entry in _directory(runtime).iterdir():
        entry.unlink()
    _directory(runtime).rmdir()

    reason = closure_evidence_drift(runtime, session=_session(), dispatch=_dispatch())

    assert reason is not None
    assert reason.startswith("closure evidence pages could not be verified")


@pytest.mark.parametrize(
    ("session_id", "generation"),
    [("session-2", 1), ("session-1", 2)],
    ids=["wrong-session", "wrong-generation"],
)
def test_directory_owned_by_another_session_is_reported(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    session_id: str,
    generation: int,
) -> None:
    _materialize(runtime, monkeypatch, session=_session(), pages=_pages())

    reason = closure_evidence_drift(
        runtime,
        session=_session(session_id, generation),
        dispatch=_dispatch(),
    )

    assert reason == "closure evidence directory is not owned by this session"


def test_inline_dispatch_has_no_drift_without_any_directory(
    runtime: SimpleNamespace,
) -> None:
    dispatch = _dispatch(payload={"body": "small inline request"})

    assert (
        closure_evidence_drift(runtime, session=_session(), dispatch=dispatch)
        is None
    )
    assert not (_workspace(runtime) / ROOT).exists()


def test_cleanup_is_not_required_for_inline_dispatch(
    runtime: SimpleNamespace, events: list[dict[str, Any]]
) -> None:
    dispatch = _dispatch(payload={"body": "small inline request"})

    disposition = cleanup_session_closure_evidence(
        runtime, _session(), dispatch, POLICY
    )

    assert disposition == "not_required"
    assert events == []


def test_cleanup_reports_absent_when_nothing_was_materialized(
    runtime: SimpleNamespace, events: list[dict[str, Any]]
) -> None:
    disposition = cleanup_session_closure_evidence(
        runtime, _session(), _dispatch(), POLICY
    )

    assert disposition == "absent"
    assert not (_workspace(runtime) / ROOT).exists()
    assert events[0]["kind"] == "diagnostic"
    assert events[0]["payload"] == {"closure_evidence_cleanup": "absent"}
    assert events[0]["replay_key"] == "closure-evidence-cleanup:session-1:1"


def test_cleanup_removes_pages_and_prunes_the_empty_root(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict[str, Any]],
) -> None:
    _materialize(runtime, monkeypatch, session=_session(), pages=_pages())

    disposition = cleanup_session_closure_evidence(
        runtime, _session(), _dispatch(), POLICY
    )

    assert disposition == "removed"
    assert not _directory(runtime).exists()
    assert not (_workspace(runtime) / ROOT).exists()
    assert _workspace(runtime).is_dir()
    assert events[0]["payload"] == {"closure_evidence_cleanup": "removed"}


def test_cleanup_keeps_a_root_that_still_holds_a_sibling_session(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict[str, Any]],
) -> None:
    sibling = materialization_component("session-2", 1)
    _materialize(runtime, monkeypatch, session=_session(), pages=_pages())
    _materialize(
        runtime,
        monkeypatch,
        session=_session("session-2"),
        pages=_pages(session_id="session-2"),
    )

    disposition = cleanup_session_closure_evidence(
        runtime, _session(), _dispatch(), POLICY
    )

    assert disposition == "removed"
    assert os.listdir(_workspace(runtime) / ROOT) == [sibling]
    assert (_workspace(runtime) / ROOT / sibling).is_dir()


def test_cleanup_refuses_a_directory_owned_by_another_session(
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict[str, Any]],
) -> None:
    sibling_pages = _pages(session_id="session-2")
    _materialize(
        runtime, monkeypatch, session=_session("session-2"), pages=sibling_pages
    )
    sibling_dispatch = _dispatch(session_id="session-2")

    disposition = cleanup_session_closure_evidence(
        runtime, _session(), sibling_dispatch, POLICY
    )

    assert disposition == "refused"
    assert events[0]["payload"] == {
        "closure_evidence_cleanup": "refused",
        "detail": "directory is not session-owned",
    }
    assert _directory(
        runtime, component=materialization_component("session-2", 1)
    ).is_dir()


def test_cleanup_refuses_a_symlinked_root_without_touching_its_target(
    tmp_path: Path,
    runtime: SimpleNamespace,
    events: list[dict[str, Any]],
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_bytes(b"keep")
    (_workspace(runtime) / ROOT).symlink_to(elsewhere, target_is_directory=True)

    disposition = cleanup_session_closure_evidence(
        runtime, _session(), _dispatch(), POLICY
    )

    assert disposition == "refused"
    assert "symlink" in events[0]["payload"]["detail"]
    assert (elsewhere / "keep.txt").read_bytes() == b"keep"


def test_materialization_refusal_diagnostic_is_bounded_and_keyed(
    events: list[dict[str, Any]], runtime: SimpleNamespace
) -> None:
    record_materialization_refusal(
        runtime,
        session=_session(),
        reason="x" * 300,
        event_redaction_policy=POLICY,
    )

    assert events[0]["kind"] == "diagnostic"
    assert events[0]["payload"] == {
        "closure_evidence_materialization_refused": "x" * 256
    }
    assert events[0]["replay_key"] == "closure-evidence-refused:session-1:1"


def test_real_refusal_reason_is_recorded_as_the_materialization_diagnostic(
    tmp_path: Path,
    runtime: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict[str, Any]],
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (_workspace(runtime) / ROOT).symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(ClosureEvidenceMaterializationError) as refused:
        _materialize(runtime, monkeypatch, session=_session(), pages=_pages())
    record_materialization_refusal(
        runtime,
        session=_session(),
        reason=str(refused.value),
        event_redaction_policy=POLICY,
    )

    assert events[-1]["payload"] == {
        "closure_evidence_materialization_refused": str(refused.value)
    }
    assert "symlink" in str(refused.value)
