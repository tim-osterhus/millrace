"""Shared SQLite+CAS runtime-store setup for substrate persistence tests."""

from __future__ import annotations

from pathlib import Path

from kernel.kernel_ping_scenarios import (
    bootstrap_to_taskmaster_claim,
    bootstrap_to_worker_claim,
)
from millrace.contracts.state import RuntimeState
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.kernel_ping import compile_kernel_ping


def runtime_store_paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "runtime.sqlite3", tmp_path / "cas"


def taskmaster_runtime_state() -> RuntimeState:
    plan, fingerprint = compile_kernel_ping()
    return bootstrap_to_taskmaster_claim(plan, fingerprint)


def worker_runtime_state() -> RuntimeState:
    plan, fingerprint = compile_kernel_ping()
    return bootstrap_to_worker_claim(plan, fingerprint)


def initialize_runtime_store(db_path: Path) -> None:
    store = SQLiteRuntimeStore.initialize(db_path)
    store.close()


def persist_runtime_state(
    db_path: Path,
    cas_root: Path,
    state: RuntimeState,
) -> None:
    store = SQLiteRuntimeStore.initialize(db_path)
    try:
        store.persist_runtime_state(state, ContentAddressedByteStore(cas_root))
    finally:
        store.close()


def load_runtime_state(db_path: Path, cas_root: Path) -> RuntimeState:
    store = SQLiteRuntimeStore.open(db_path)
    try:
        return store.load_runtime_state(ContentAddressedByteStore(cas_root))
    finally:
        store.close()


def persist_and_load_runtime_state(
    tmp_path: Path,
    state: RuntimeState,
) -> RuntimeState:
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    return load_runtime_state(db_path, cas_root)


def persist_taskmaster_runtime_state(
    tmp_path: Path,
) -> tuple[Path, Path, RuntimeState]:
    state = taskmaster_runtime_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    return db_path, cas_root, state


def persist_worker_runtime_state(
    tmp_path: Path,
) -> tuple[Path, Path, RuntimeState]:
    state = worker_runtime_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    return db_path, cas_root, state
