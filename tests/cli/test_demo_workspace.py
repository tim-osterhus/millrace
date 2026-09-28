from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from millrace.adapters.cli.demo_workspace import (
    CommandHome,
    OwnershipRefusal,
    strict_id,
)


def test_receipt_lock_and_replaced_receipt_refusal(tmp_path: Path) -> None:
    home = CommandHome(tmp_path / "demo", create=True)
    expected = {"candidate": "one"}
    demo = home.create(expected)
    home.acquire(demo, expected)
    with pytest.raises(OwnershipRefusal, match="lock_collision"):
        home.acquire(demo, expected)
    path = home.path / "workspaces" / demo / "receipt.json"
    prior = path.read_bytes()
    forged = json.loads(prior)
    forged["state"] = "complete"
    path.write_text(json.dumps(forged))
    with pytest.raises(OwnershipRefusal, match="unowned_receipt"):
        home.verify(demo, expected)
    path.write_bytes(prior)
    home.release(demo, expected)
    home.close()


@pytest.mark.parametrize(
    "value", ["../x", "/tmp/x", "", "00000000-0000-0000-0000-00000000000X"]
)
def test_resume_id_is_never_a_path(value: str) -> None:
    with pytest.raises(OwnershipRefusal):
        strict_id(value)


def test_command_root_replacement_and_unknown_lock_refuse(tmp_path: Path) -> None:
    home = CommandHome(tmp_path / "demo", create=True)
    demo = home.create({})
    home.acquire(demo, {})
    home.close()  # Simulated process loss keeps the durable lock.
    reopened = CommandHome(tmp_path / "demo")
    with pytest.raises(OwnershipRefusal, match="lock_collision"):
        reopened.acquire(demo, {})
    os.rename(tmp_path / "demo", tmp_path / "prior")
    (tmp_path / "demo").mkdir(mode=0o700)
    with pytest.raises(OwnershipRefusal, match="root_identity"):
        reopened.verify(demo, {})
    reopened.close()


def test_symlink_parent_and_unsafe_root_refused(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    with pytest.raises((ValueError, OSError)):
        CommandHome(tmp_path / "link/demo", create=True)
