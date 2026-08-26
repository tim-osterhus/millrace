from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from tests.adapters.test_context_checkout import _plan_with_all_context_sources

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.adapters.cli.context import CliWorkspacePaths
from millrace.adapters.cli.context_checkout import prepare_context_checkout
from millrace.compiler import authority_fingerprint
from millrace.substrate.cas import ContentAddressedByteStore
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
