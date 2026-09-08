from __future__ import annotations

import io
import json
import multiprocessing
import stat
import threading
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from tests.adapters.test_context_checkout import _plan_with_all_context_sources
from tests.adapters.test_context_writeback import _bound_fixture

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.adapters.cli.context import CliWorkspacePaths
from millrace.adapters.cli.context_checkout import prepare_context_checkout
from millrace.compiler import authority_fingerprint
from millrace.substrate.cas import ContentAddressedByteStore, storage_digest_for_bytes
from millrace.testing import fake_runner_session_state


@dataclass(frozen=True)
class _ContextFixture:
    workspace: Path
    db_path: Path
    cas_path: Path
    checkout: Path
    manifest_digest: str
    session_id: str
    session_fence: str
    catalog_paths: tuple[str, ...]


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    from millrace.adapters.cli.main import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _json(raw: str) -> dict[str, Any]:
    assert raw.endswith("\n")
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def _fixture(
    tmp_path: Path,
    *,
    max_hydrated_files: int = 16,
    max_hydrated_bytes: int = 16_384,
) -> _ContextFixture:
    plan, _unused_fingerprint = _plan_with_all_context_sources(
        accepted_discoverable=True,
        workspace_discoverable=True,
    )
    binding = replace(
        plan.context_bindings[0],
        max_hydrated_files=max_hydrated_files,
        max_hydrated_bytes=max_hydrated_bytes,
    )
    plan = replace(plan, context_bindings=(binding,))
    fingerprint = authority_fingerprint(plan)
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]

    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "a.txt").write_bytes(b"one\n")
    (workspace / "docs" / "b.txt").write_bytes(b"two\n")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir(parents=True)
    db_path.touch()
    cas_path.mkdir(parents=True)
    cas_store = ContentAddressedByteStore(cas_path)
    paths = CliWorkspacePaths(workspace, db_path, cas_path)
    prepared = prepare_context_checkout(
        paths=paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=binding,
        state=state,
        cas_store=cas_store,
    )
    from millrace.adapters.cli import session_completion
    from millrace.adapters.cli.context import (
        OpenRuntimeContext,
        contextual_input_id,
    )
    from millrace.contracts.transition import AttachRunnerSessionContext
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, cas_store)
        runtime = OpenRuntimeContext(
            paths=paths,
            store=store,
            cas_store=cas_store,
        )
        attachment = AttachRunnerSessionContext(
            f"cli:run.session-context-attach:{session.session_id}",
            run_ref=state.runs[session.run_id].run_ref,
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            context_manifest_digest=prepared.manifest_digest,
            selected_binding_id=str(binding.id),
        )
        persisted = session_completion._persist_transition(
            runtime,
            replace(attachment, input_id=contextual_input_id(attachment)),
        )
        assert persisted is not None
    finally:
        store.close()
    catalog_paths = tuple(
        item.logical_path for item in prepared.manifest.catalog
        if item.source_kind == "workspace_relative_root"
    )
    assert len(catalog_paths) == 2
    return _ContextFixture(
        workspace=workspace,
        db_path=db_path,
        cas_path=cas_path,
        checkout=prepared.materialized_checkout_root,
        manifest_digest=prepared.manifest_digest,
        session_id=session.session_id,
        session_fence=session.session_fencing_token,
        catalog_paths=tuple(sorted(catalog_paths, key=lambda value: value.encode())),
    )


def _select(
    fixture: _ContextFixture,
    *paths: str,
    session_id: str | None = None,
    manifest_digest: str | None = None,
) -> tuple[int, str, str]:
    args = [
        "--json",
        "--workspace",
        str(fixture.workspace),
        "context",
        "select",
        "--session-id",
        fixture.session_id if session_id is None else session_id,
        "--manifest-digest",
        fixture.manifest_digest if manifest_digest is None else manifest_digest,
    ]
    for path in paths:
        args.extend(("--path", path))
    return _invoke(args)


def _diff(
    workspace: Path,
    *,
    session_id: str,
    manifest_digest: str,
) -> tuple[int, str, str]:
    return _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "context",
            "diff",
            "--session-id",
            session_id,
            "--manifest-digest",
            manifest_digest,
        ]
    )


def _receipts(fixture: _ContextFixture):
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    store = SQLiteRuntimeStore.open(fixture.db_path)
    try:
        return store.load_context_hydration_receipts_authenticated(
            fixture.session_id,
            1,
            fixture.session_fence,
        )
    finally:
        store.close()


def _assert_refused(result: tuple[int, str, str]) -> dict[str, Any]:
    exit_code, stdout, stderr = result
    assert exit_code == 3
    assert stdout == ""
    payload = _json(stderr)
    assert payload["ok"] is False
    assert payload["command"] == "context.select"
    return payload


def test_context_diff_projects_exact_changes_without_persisting_state(
    tmp_path: Path,
) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    workspace = runtime.paths.workspace_path
    path = workspace / "src" / "existing.txt"
    before_digest = storage_digest_for_bytes(path.read_bytes())
    path.write_text("after\n", encoding="utf-8")
    manifest_digest = session.context_manifest_digest
    assert manifest_digest is not None
    runtime.close()

    exit_code, stdout, stderr = _diff(
        workspace,
        session_id=session.session_id,
        manifest_digest=manifest_digest,
    )

    assert exit_code == 0, stderr
    assert stderr == ""
    payload = _json(stdout)
    assert payload["command"] == "context.diff"
    assert payload["code"] == "context_diff_projected"
    assert payload["data"] == {
        "session_id": session.session_id,
        "manifest_digest": manifest_digest,
        "changes": [
            {
                "path": "src/existing.txt",
                "change_kind": "modify",
                "before_sha256": before_digest,
                "after_sha256": storage_digest_for_bytes(path.read_bytes()),
            }
        ],
    }
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    store = SQLiteRuntimeStore.open(workspace / ".millrace" / "runtime.sqlite3")
    try:
        assert store.load_runtime_state(
            ContentAddressedByteStore(workspace / ".millrace" / "cas")
        ) == state
    finally:
        store.close()


def test_context_diff_refuses_stale_manifest(tmp_path: Path) -> None:
    runtime, _state, session, _binding = _bound_fixture(tmp_path)
    workspace = runtime.paths.workspace_path
    runtime.close()

    exit_code, stdout, stderr = _diff(
        workspace,
        session_id=session.session_id,
        manifest_digest="sha256:" + "0" * 64,
    )

    assert exit_code == 3
    assert stdout == ""
    payload = _json(stderr)
    assert payload["command"] == "context.diff"
    assert payload["code"] == "context_diff_refused"


def _overlapping_selects(
    monkeypatch,
    fixture: _ContextFixture,
    *,
    force_limit_race: bool = False,
) -> dict[str, tuple[int, str, str]]:
    from millrace.adapters.cli import context_selection
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    context = multiprocessing.get_context("fork")
    coordinated = context.Barrier(2)
    first_prepared = context.Event()
    first_restored = context.Event()
    second_attempting_lock = context.Event()
    second_acquired_lock = context.Event()
    results = context.Queue()
    original_acquire_lock = context_selection._acquire_selection_lock
    original_prepare = context_selection._prepare_selection_directories
    original_restore = context_selection._restore_selection_modes
    original_validate_limits = context_selection._validate_cumulative_limits
    original_record_receipt = SQLiteRuntimeStore.record_context_hydration_receipt
    overlap = {"observed": False}

    def observed_acquire_lock(final_root: Path) -> int:
        is_second = multiprocessing.current_process().name == "selection-second"
        if is_second:
            second_attempting_lock.set()
        descriptor = original_acquire_lock(final_root)
        if is_second:
            second_acquired_lock.set()
        return descriptor

    def rendezvous(barrier, *, announce_first: bool) -> bool:
        is_first = multiprocessing.current_process().name == "selection-first"
        if is_first and announce_first:
            first_prepared.set()
            if not second_attempting_lock.wait(timeout=5.0):
                raise RuntimeError("second selection did not attempt the session lock")
        try:
            barrier.wait(timeout=2.0)
        except threading.BrokenBarrierError:
            if is_first and second_acquired_lock.is_set():
                raise RuntimeError(
                    "second selection acquired the lock but missed the rendezvous"
                )
            return False
        return True

    def coordinated_prepare(**kwargs) -> None:
        original_prepare(**kwargs)
        overlap["observed"] = rendezvous(coordinated, announce_first=True)

    def coordinated_restore(**kwargs) -> None:
        if not overlap["observed"]:
            original_restore(**kwargs)
            return
        if multiprocessing.current_process().name == "selection-first":
            original_restore(**kwargs)
            first_restored.set()
            return
        if not first_restored.wait(timeout=5.0):
            raise RuntimeError("first overlapping selection did not restore")
        original_restore(**kwargs)

    if force_limit_race:
        receipts_recorded = context.Barrier(2)

        def coordinated_validate_limits(**kwargs) -> None:
            original_validate_limits(**kwargs)
            rendezvous(coordinated, announce_first=True)

        def coordinated_record_receipt(self, receipt):
            stored = original_record_receipt(self, receipt)
            rendezvous(receipts_recorded, announce_first=False)
            return stored

        monkeypatch.setattr(
            context_selection,
            "_validate_cumulative_limits",
            coordinated_validate_limits,
        )
        monkeypatch.setattr(
            SQLiteRuntimeStore,
            "record_context_hydration_receipt",
            coordinated_record_receipt,
        )
    else:
        monkeypatch.setattr(
            context_selection,
            "_prepare_selection_directories",
            coordinated_prepare,
        )
        monkeypatch.setattr(
            context_selection,
            "_restore_selection_modes",
            coordinated_restore,
        )
    monkeypatch.setattr(
        context_selection,
        "_acquire_selection_lock",
        observed_acquire_lock,
    )

    def select(label: str, catalog_path: str) -> None:
        results.put((label, _select(fixture, catalog_path)))

    first = context.Process(
        name="selection-first",
        target=select,
        args=("first", fixture.catalog_paths[0]),
    )
    second = context.Process(
        name="selection-second",
        target=select,
        args=("second", fixture.catalog_paths[1]),
    )
    processes = (first, second)
    first.start()
    try:
        assert first_prepared.wait(timeout=5.0)
        second.start()
        for process in processes:
            process.join(timeout=15.0)
        assert all(not process.is_alive() for process in processes)
        assert all(process.exitcode == 0 for process in processes)
        return dict(results.get(timeout=2.0) for _process in processes)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5.0)


def _context_writeback_result(fixture: _ContextFixture) -> str | None:
    from millrace.adapters.cli.context import CliWorkspacePaths, OpenRuntimeContext
    from millrace.adapters.cli.context_writeback import validate_context_writeback
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    store = SQLiteRuntimeStore.open(fixture.db_path)
    runtime = OpenRuntimeContext(
        paths=CliWorkspacePaths(
            fixture.workspace,
            fixture.db_path,
            fixture.cas_path,
        ),
        store=store,
        cas_store=ContentAddressedByteStore(fixture.cas_path),
    )
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        return validate_context_writeback(
            runtime,
            session=state.runner_sessions[fixture.session_id],
            evidence=None,
        )
    finally:
        runtime.close()


def _assert_checkout_directories_are_read_only(fixture: _ContextFixture) -> None:
    directories = (
        fixture.checkout,
        *(path for path in fixture.checkout.rglob("*") if path.is_dir()),
    )
    assert all(stat.S_IMODE(path.lstat().st_mode) == 0o555 for path in directories)


def test_context_select_hydrates_declared_paths_and_replays_idempotently(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    first = _select(
        fixture,
        fixture.catalog_paths[1],
        fixture.catalog_paths[0],
        fixture.catalog_paths[1],
    )
    first_code, first_stdout, first_stderr = first
    assert first_code == 0, first_stderr
    assert first_stderr == ""
    first_payload = _json(first_stdout)
    assert first_payload["ok"] is True
    assert first_payload["command"] == "context.select"
    assert set(first_payload["data"]) == {"selected"}
    selected = first_payload["data"]["selected"]
    assert [item["catalog_path"] for item in selected] == list(fixture.catalog_paths)
    assert all(
        set(item) == {"catalog_path", "content_digest", "byte_length", "receipt_id"}
        for item in selected
    )
    assert str(fixture.workspace) not in first_stdout
    assert fixture.session_fence not in first_stdout
    assert "session_fencing_token" not in first_stdout

    for path in fixture.catalog_paths:
        selected_path = fixture.checkout / "selected" / path
        assert selected_path.read_bytes() in {b"one\n", b"two\n"}
        assert selected_path.stat().st_mode & 0o777 == 0o444
    assert len(_receipts(fixture)) == 2

    replay = _select(
        fixture,
        fixture.catalog_paths[1],
        fixture.catalog_paths[0],
        fixture.catalog_paths[1],
    )
    assert replay[0] == 0, replay[2]
    assert _json(replay[1]) == first_payload
    assert len(_receipts(fixture)) == 2


def test_context_select_serializes_overlapping_legal_selections(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _fixture(tmp_path)

    results = _overlapping_selects(monkeypatch, fixture)

    assert results["first"][0] == 0, results["first"][2]
    assert results["second"][0] == 0, results["second"][2]
    assert len(_receipts(fixture)) == 2
    _assert_checkout_directories_are_read_only(fixture)
    assert _context_writeback_result(fixture) is None


def test_context_select_serializes_cumulative_limit_check(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _fixture(tmp_path, max_hydrated_files=1)

    results = _overlapping_selects(
        monkeypatch,
        fixture,
        force_limit_race=True,
    )

    assert results["first"][0] == 0, results["first"][2]
    refused = _assert_refused(results["second"])
    assert refused["code"] == "context_selection_refused"
    receipts = _receipts(fixture)
    assert len(receipts) == 1
    assert receipts[0].catalog_path == fixture.catalog_paths[0]
    assert not (fixture.checkout / "selected" / fixture.catalog_paths[1]).exists()
    _assert_checkout_directories_are_read_only(fixture)
    assert _context_writeback_result(fixture) is None


def test_context_select_receipts_survive_rematerialization_and_writeback_verification(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    selected = _select(fixture, fixture.catalog_paths[0])
    assert selected[0] == 0, selected[2]

    from millrace.adapters.cli.context import CliWorkspacePaths, OpenRuntimeContext
    from millrace.adapters.cli.context_checkout import (
        rematerialize_attached_context_checkout,
    )
    from millrace.adapters.cli.context_writeback import validate_context_writeback
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    store = SQLiteRuntimeStore.open(fixture.db_path)
    runtime = OpenRuntimeContext(
        paths=CliWorkspacePaths(
            fixture.workspace,
            fixture.db_path,
            fixture.cas_path,
        ),
        store=store,
        cas_store=ContentAddressedByteStore(fixture.cas_path),
    )
    try:
        state = runtime.store.load_runtime_state(runtime.cas_store)
        session = state.runner_sessions[fixture.session_id]
        run = state.runs[session.run_id]
        fingerprint = run.run_ref.plan_ref.authority_fingerprint
        binding = next(
            binding
            for binding in state.admitted_plans[
                fingerprint
            ].selected_plan.context_bindings
            if str(binding.stage_kind_id) == str(run.stage_kind_id)
        )
        receipts = runtime.store.load_context_hydration_receipts_authenticated(
            session.session_id,
            session.dispatch_generation,
            session.session_fencing_token,
        )

        rematerialized = rematerialize_attached_context_checkout(
            paths=runtime.paths,
            session=session,
            plan_fingerprint=fingerprint,
            binding=binding,
            manifest_digest=fixture.manifest_digest,
            state=state,
            cas_store=runtime.cas_store,
            hydration_receipts=receipts,
        )

        assert rematerialized.materialized_checkout_root == fixture.checkout
        assert validate_context_writeback(
            runtime,
            session=session,
            evidence=None,
        ) is None
    finally:
        runtime.close()


def test_context_select_enforces_cumulative_file_limit(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, max_hydrated_files=1)
    first = _select(fixture, fixture.catalog_paths[0])
    assert first[0] == 0, first[2]

    refused = _assert_refused(_select(fixture, fixture.catalog_paths[1]))
    assert refused["code"] == "context_selection_refused"
    assert len(_receipts(fixture)) == 1
    assert not (fixture.checkout / "selected" / fixture.catalog_paths[1]).exists()


def test_context_select_enforces_cumulative_byte_limit(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, max_hydrated_bytes=5)
    first = _select(fixture, fixture.catalog_paths[0])
    assert first[0] == 0, first[2]

    _assert_refused(_select(fixture, fixture.catalog_paths[1]))
    assert len(_receipts(fixture)) == 1


def test_context_select_refuses_undeclared_path(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    _assert_refused(_select(fixture, "discoverable/workspace/docs/missing.txt"))
    assert _receipts(fixture) == ()


def test_context_select_refuses_foreign_session(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    _assert_refused(_select(fixture, fixture.catalog_paths[0], session_id="foreign"))
    assert _receipts(fixture) == ()


def test_context_select_refuses_inactive_session(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    store = SQLiteRuntimeStore.open(fixture.db_path)
    try:
        store._connection.execute(
            """
            UPDATE runner_sessions
            SET state = 'completed', start_intent_at = 1, started_at = 2,
                ended_at = 3, cleanup_disposition = 'not_required'
            WHERE session_id = ?
            """,
            (fixture.session_id,),
        )
        store._connection.commit()
    finally:
        store.close()

    _assert_refused(_select(fixture, fixture.catalog_paths[0]))
    assert _receipts(fixture) == ()


def test_context_select_refuses_stale_manifest(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    _assert_refused(
        _select(
            fixture,
            fixture.catalog_paths[0],
            manifest_digest="sha256:" + "0" * 64,
        )
    )
    assert _receipts(fixture) == ()


def test_context_select_refuses_missing_catalog_cas(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    from millrace.contracts.context_checkout import decode_context_checkout_manifest

    manifest = decode_context_checkout_manifest(
        (fixture.checkout / "checkout.manifest.json").read_bytes()
    )
    entry = next(
        item
        for item in manifest.catalog
        if item.logical_path == fixture.catalog_paths[0]
    )
    (
        fixture.cas_path
        / "sha256"
        / entry.content_digest.removeprefix("sha256:")
    ).unlink()

    _assert_refused(_select(fixture, fixture.catalog_paths[0]))
    assert _receipts(fixture) == ()


def test_context_select_refuses_symlinked_target(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    target = fixture.checkout / "selected" / fixture.catalog_paths[0]
    fixture.checkout.chmod(0o755)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(tmp_path / "outside")
    for path in (fixture.checkout, *fixture.checkout.rglob("*")):
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o555)
        elif path.is_file():
            path.chmod(0o444)

    _assert_refused(_select(fixture, fixture.catalog_paths[0]))
    assert _receipts(fixture) == ()


def test_context_select_refuses_catalog_digest_drift(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    from millrace.contracts.context_checkout import decode_context_checkout_manifest

    manifest = decode_context_checkout_manifest(
        (fixture.checkout / "checkout.manifest.json").read_bytes()
    )
    entry = next(
        item
        for item in manifest.catalog
        if item.logical_path == fixture.catalog_paths[0]
    )
    (
        fixture.cas_path
        / "sha256"
        / entry.content_digest.removeprefix("sha256:")
    ).write_bytes(b"drifted")

    _assert_refused(_select(fixture, fixture.catalog_paths[0]))
    assert _receipts(fixture) == ()


def test_context_select_projects_hydration_totals_in_session_status(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    selected = fixture.catalog_paths[0]
    result = _select(fixture, selected)
    assert result[0] == 0, result[2]

    code, stdout, stderr = _invoke(
        ["--json", "--workspace", str(fixture.workspace), "status"]
    )
    assert code == 0, stderr
    projection = _json(stdout)["data"]["runner_sessions"]
    session = next(
        item for item in projection if item["session_id"] == fixture.session_id
    )
    assert session["hydrated_file_count"] == 1
    assert session["hydrated_bytes"] == 4
    assert "session_fencing_token" not in json.dumps(session)
    assert fixture.session_fence not in json.dumps(session)
    assert str(fixture.workspace) not in json.dumps(session)
