"""Private command-owned lifecycle prototype, local single-operator boundary.

OS uid/modes and separately retained command registry authenticate receipts.
No claim of protection from arbitrary same-uid Python or command-root tampering.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any


class OwnershipRefusal(ValueError):
    pass


def canonical(v: Any) -> Any:
    return json.dumps(v, sort_keys=True, separators=(",", ":")).encode()


def sha(v: Any) -> Any:
    return hashlib.sha256(canonical(v)).hexdigest()


def check(st: Any, directory: Any = False) -> Any:
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 63:
        raise OwnershipRefusal("ownership_or_permissions")
    if directory and (not stat.S_ISDIR(st.st_mode)):
        raise OwnershipRefusal("not_directory")
    if not directory and (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1):
        raise OwnershipRefusal("not_private_regular_file")


def identity(st: Any) -> Any:
    return [st.st_dev, st.st_ino, st.st_uid]


def strict_id(v: Any) -> Any:
    try:
        valid = str(uuid.UUID(v)) == v
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise OwnershipRefusal("invalid_demo_id")
    return v


def read_at(fd: Any, name: Any) -> Any:
    try:
        f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    except OSError as e:
        raise OwnershipRefusal("unsafe_or_missing_file") from e
    try:
        check(os.fstat(f))
        b = os.read(f, 1024 * 1024)
        if len(b) >= 1024 * 1024:
            raise OwnershipRefusal("oversized_record")
        return (json.loads(b), identity(os.fstat(f)))
    finally:
        os.close(f)


def write_at(fd: Any, name: Any, value: Any, exclusive: Any = False) -> Any:
    temp = name if exclusive else ".new-" + uuid.uuid4().hex
    f = os.open(
        temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 384, dir_fd=fd
    )
    try:
        payload = memoryview(canonical(value))
        while payload:
            payload = payload[os.write(f, payload) :]
        os.fsync(f)
    finally:
        os.close(f)
    if not exclusive:
        os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
    os.fsync(fd)


class CommandHome:
    def __init__(self, path: Any, *, create: Any = False) -> None:
        self.path = Path(path)
        from millrace.adapters.cli.setup_receipts import secure_directory

        parent_fd = secure_directory(self.path.parent, create=create)
        os.close(parent_fd)
        if create:
            self.path.mkdir(mode=448, exist_ok=False)
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        check(os.fstat(self.fd), True)
        self.root_identity = identity(os.fstat(self.fd))
        if create:
            os.mkdir("workspaces", 448, dir_fd=self.fd)
            os.mkdir("authority", 448, dir_fd=self.fd)
        self.wfd = os.open(
            "workspaces", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd
        )
        self.afd = os.open(
            "authority", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd
        )
        check(os.fstat(self.wfd), True)
        check(os.fstat(self.afd), True)
        self.locks: dict[str, Any] = {}

    def close(self) -> Any:
        for fd in [self.wfd, self.afd, self.fd]:
            os.close(fd)

    def current_root(self) -> Any:
        st = os.lstat(self.path)
        if stat.S_ISLNK(st.st_mode) or identity(st) != self.root_identity:
            raise OwnershipRefusal("root_identity_changed")
        check(st, True)
        for n, fd in [("workspaces", self.wfd), ("authority", self.afd)]:
            st = os.stat(n, dir_fd=self.fd, follow_symlinks=False)
            if identity(st) != identity(os.fstat(fd)) or not stat.S_ISDIR(st.st_mode):
                raise OwnershipRefusal("owned_parent_changed")
            check(st, True)

    def create(self, expected: Any) -> Any:
        self.current_root()
        demo = str(uuid.uuid4())
        os.mkdir(demo, 448, dir_fd=self.wfd)
        fd = os.open(
            demo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.wfd
        )
        try:
            receipt = {
                "demo_id": demo,
                "root": str(self.path.resolve()),
                "root_identity": self.root_identity,
                "workspace_identity": identity(os.fstat(fd)),
                "command": "Core.demo.command.v1",
                "identity": expected,
                "state": "created",
                "runtime": None,
            }
            write_at(fd, "receipt.json", receipt, True)
            write_at(
                self.afd,
                demo + ".json",
                {"receipt": receipt, "receipt_hash": sha(receipt)},
                True,
            )
        finally:
            os.close(fd)
        return demo

    def verify(self, demo: Any, expected: Any, *, incomplete: Any = True) -> Any:
        strict_id(demo)
        self.current_root()
        (authority, _) = read_at(self.afd, demo + ".json")
        try:
            fd = os.open(
                demo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.wfd
            )
        except OSError as e:
            raise OwnershipRefusal("workspace_unsafe") from e
        try:
            check(os.fstat(fd), True)
            (r, _) = read_at(fd, "receipt.json")
            if r != authority["receipt"] or sha(r) != authority["receipt_hash"]:
                raise OwnershipRefusal("unowned_receipt")
            if (
                r["demo_id"] != demo
                or r["command"] != "Core.demo.command.v1"
                or r["root"] != str(self.path.resolve())
                or (r["root_identity"] != self.root_identity)
                or (r["workspace_identity"] != identity(os.fstat(fd)))
            ):
                raise OwnershipRefusal("identity_mismatch")
            if r["identity"] != expected:
                raise OwnershipRefusal("incompatible_identity")
            if incomplete and r["state"] in [
                "complete",
                "cleaned",
                "cleanup_incomplete",
            ]:
                raise OwnershipRefusal("complete_demo")
            return r
        finally:
            os.close(fd)

    def reopen_verified(self, demo: Any, r: Any) -> Any:
        """Anchor mutations to the exact directory authenticated by verify."""
        self.current_root()
        try:
            fd = os.open(
                demo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.wfd
            )
        except OSError as e:
            raise OwnershipRefusal("workspace_unsafe") from e
        try:
            check(os.fstat(fd), True)
            if identity(os.fstat(fd)) != r["workspace_identity"]:
                raise OwnershipRefusal("workspace_replaced")
            (current, _) = read_at(fd, "receipt.json")
            (authority, _) = read_at(self.afd, demo + ".json")
            if current != r or authority != {"receipt": r, "receipt_hash": sha(r)}:
                raise OwnershipRefusal("authority_changed")
            if identity(
                os.stat(demo, dir_fd=self.wfd, follow_symlinks=False)
            ) != identity(os.fstat(fd)):
                raise OwnershipRefusal("workspace_replaced")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def persist_receipt(self, demo: Any, r: Any, updated: Any) -> Any:
        fd = self.reopen_verified(demo, r)
        try:
            write_at(fd, "receipt.json", updated)
        finally:
            os.close(fd)
        write_at(
            self.afd, demo + ".json", {"receipt": updated, "receipt_hash": sha(updated)}
        )
        return updated

    def update(
        self, demo: Any, expected: Any, *, state: Any, runtime: Any = None
    ) -> Any:
        if state not in {"created", "interrupted", "running", "cancelled"}:
            raise OwnershipRefusal("caller_completion_not_authority")
        r = self.verify(demo, expected)
        return self.persist_receipt(demo, r, {**r, "state": state, "runtime": runtime})

    def completion_evidence(self, demo: Any, r: Any) -> Any:
        from millrace.adapters.cli.demo import completion_evidence

        runtime = self.open_runtime(demo, r["identity"], r)
        try:
            result = completion_evidence(runtime)
        finally:
            runtime.close()
        root = self.path / "workspaces" / demo / "runtime"
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            result["runtime_identity"] = identity(os.fstat(fd))
            result["inventory_sha256"] = sha(self.inventory(fd))
        finally:
            os.close(fd)
        return result

    def validate_lock(self, demo: Any, expected: Any) -> Any:
        r = self.verify(demo, expected, incomplete=False)
        if demo not in self.locks:
            raise OwnershipRefusal("foreground_lock_required")
        fd = self.reopen_verified(demo, r)
        try:
            (lock, ident) = read_at(fd, "lock.json")
            if (lock["token"], ident) != self.locks[demo]:
                raise OwnershipRefusal("lock_replaced")
        finally:
            os.close(fd)

    def open_runtime(self, demo: Any, expected: Any, r: Any) -> Any:
        from millrace.adapters.cli.demo_owned_runtime import open_owned_runtime

        return open_owned_runtime(
            self.path / "workspaces" / demo / "runtime",
            validate_owner=lambda: self.validate_lock(demo, expected),
            expected=r["runtime"]["owned_identity"],
        )

    def complete(self, demo: Any, expected: Any) -> Any:
        r = self.verify(demo, expected)
        evidence = self.completion_evidence(demo, r)
        fd = self.reopen_verified(demo, r)
        try:
            runtime_fd = os.open(
                "runtime", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            try:
                if identity(os.fstat(runtime_fd)) != evidence["runtime_identity"]:
                    raise OwnershipRefusal("runtime_replaced")
                inventory = self.inventory(runtime_fd)
            finally:
                os.close(runtime_fd)
        finally:
            os.close(fd)
        if sha(inventory) != evidence["inventory_sha256"]:
            raise OwnershipRefusal("unstable_store_snapshot")
        return self.persist_receipt(
            demo,
            r,
            {
                **r,
                "state": "complete",
                "completion_evidence": evidence,
                "cleanup_inventory": inventory,
            },
        )

    def acquire(self, demo: Any, expected: Any) -> Any:
        r = self.verify(demo, expected)
        fd = self.reopen_verified(demo, r)
        token = uuid.uuid4().hex
        try:
            try:
                write_at(fd, "lock.json", {"token": token, "pid": os.getpid()}, True)
            except FileExistsError as e:
                raise OwnershipRefusal("lock_collision_or_unknown_owner") from e
            (_, ino) = read_at(fd, "lock.json")
            self.locks[demo] = (token, ino)
        finally:
            os.close(fd)

    def release(self, demo: Any, expected: Any) -> Any:
        r = self.verify(demo, expected, incomplete=False)
        owned = self.locks.get(demo)
        if owned is None:
            raise OwnershipRefusal("lock_not_owned")
        fd = self.reopen_verified(demo, r)
        try:
            (lock, ino) = read_at(fd, "lock.json")
            if (lock["token"], ino) != owned:
                raise OwnershipRefusal("lock_replaced")
            os.unlink("lock.json", dir_fd=fd)
            os.fsync(fd)
            del self.locks[demo]
        finally:
            os.close(fd)

    def reopen_and_close(self, demo: Any, r: Any) -> Any:
        fd = self.reopen_verified(demo, r)
        os.close(fd)

    @staticmethod
    def capture_tree(root_fd: Any, expected: Any = None) -> Any:
        """No path-based read: open each child relative to an anchored directory."""
        inventory = {}
        payloads = {}
        budget = [0, 0]

        def walk(fd: Any, prefix: Any) -> Any:
            names = sorted(os.listdir(fd))
            if expected is not None:
                allowed = {
                    k[len(prefix) :].split("/")[0]
                    for k in expected
                    if k.startswith(prefix)
                }
                if set(names) != allowed:
                    raise OwnershipRefusal("unknown_or_missing_inventory_member")
            for name in names:
                budget[0] += 1
                if budget[0] > 4096:
                    raise OwnershipRefusal("snapshot_file_limit")
                rel = prefix + name
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                isdir = stat.S_ISDIR(st.st_mode)
                if st.st_uid != os.getuid() or (
                    not isdir and (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1)
                ):
                    raise OwnershipRefusal("unsafe_inventory_member")
                if not isdir:
                    budget[1] += st.st_size
                    if st.st_size > 8 * 1024 * 1024 or budget[1] > 32 * 1024 * 1024:
                        raise OwnershipRefusal("snapshot_byte_limit")
                entry = {
                    "identity": identity(st),
                    "kind": "directory" if isdir else "file",
                    "sha256": None,
                }
                if expected is not None and (
                    entry["identity"] != expected[rel]["identity"]
                    or entry["kind"] != expected[rel]["kind"]
                ):
                    raise OwnershipRefusal("inventory_identity_changed")
                flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if isdir else 0)
                try:
                    child = os.open(name, flags, dir_fd=fd)
                except OSError as e:
                    raise OwnershipRefusal("inventory_open_refused") from e
                try:
                    actual = os.fstat(child)
                    if identity(actual) != entry["identity"] or stat.S_IFMT(
                        actual.st_mode
                    ) != stat.S_IFMT(st.st_mode):
                        raise OwnershipRefusal("inventory_open_race")
                    if isdir:
                        walk(child, rel + "/")
                    else:
                        chunks = []
                        while True:
                            chunk = os.read(child, 1024 * 1024)
                            if not chunk:
                                break
                            chunks.append(chunk)
                        data = b"".join(chunks)
                        entry["sha256"] = hashlib.sha256(data).hexdigest()
                        payloads[rel] = data
                        if (
                            expected is not None
                            and entry["sha256"] != expected[rel]["sha256"]
                        ):
                            raise OwnershipRefusal("inventory_bytes_changed")
                    if (
                        identity(os.stat(name, dir_fd=fd, follow_symlinks=False))
                        != entry["identity"]
                    ):
                        raise OwnershipRefusal("inventory_postread_race")
                    inventory[rel] = entry
                finally:
                    os.close(child)

        walk(root_fd, "")
        return (inventory, payloads)

    @staticmethod
    def inventory(root: Any) -> Any:
        try:
            fd = (
                os.dup(root)
                if isinstance(root, int)
                else os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            )
        except OSError as e:
            raise OwnershipRefusal("unsafe_inventory_root") from e
        try:
            return CommandHome.capture_tree(fd)[0]
        finally:
            os.close(fd)

    def cleanup(self, demo: Any, expected: Any) -> Any:
        r = self.verify(demo, expected, incomplete=False)
        if r["state"] != "complete":
            raise OwnershipRefusal("unknown_aftermath_retained")
        if self.completion_evidence(demo, r) != r.get("completion_evidence"):
            raise OwnershipRefusal("completion_changed")
        fd = self.reopen_verified(demo, r)
        deleted = []
        try:
            self.validate_lock(demo, expected)
            if set(os.listdir(fd)) - {"receipt.json", "runtime", "lock.json"}:
                raise OwnershipRefusal("unknown_member_retained")
            allowed = r["cleanup_inventory"]
            runtime_fd = os.open(
                "runtime", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            try:
                if (
                    identity(os.fstat(runtime_fd))
                    != r["completion_evidence"]["runtime_identity"]
                ):
                    raise OwnershipRefusal("runtime_replaced")
                if self.inventory(runtime_fd) != allowed:
                    raise OwnershipRefusal("unknown_runtime_aftermath_retained")

                def checked(parent: Any, name: Any, entry: Any) -> Any:
                    st = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    kind = (
                        "directory"
                        if stat.S_ISDIR(st.st_mode)
                        else "file"
                        if stat.S_ISREG(st.st_mode) and st.st_nlink == 1
                        else "unsafe"
                    )
                    if identity(st) != entry["identity"] or kind != entry["kind"]:
                        raise OwnershipRefusal("cleanup_identity_changed")

                def remove(parent: Any, prefix: Any) -> Any:
                    children = {
                        k[len(prefix) :]: v
                        for (k, v) in allowed.items()
                        if k.startswith(prefix) and "/" not in k[len(prefix) :]
                    }
                    if set(os.listdir(parent)) != set(children):
                        raise OwnershipRefusal("unknown_cleanup_member_retained")
                    for name, entry in sorted(children.items()):
                        rel = prefix + name
                        checked(parent, name, entry)
                        if entry["kind"] == "directory":
                            child = os.open(
                                name,
                                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=parent,
                            )
                            try:
                                if identity(os.fstat(child)) != entry["identity"]:
                                    raise OwnershipRefusal(
                                        "cleanup_directory_open_race"
                                    )
                                remove(child, rel + "/")
                            finally:
                                os.close(child)
                            checked(parent, name, entry)
                            os.rmdir(name, dir_fd=parent)
                            deleted.append(rel)
                        else:
                            child = os.open(
                                name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent
                            )
                            try:
                                if identity(os.fstat(child)) != entry["identity"]:
                                    raise OwnershipRefusal("cleanup_file_open_race")
                                chunks = []
                                while True:
                                    chunk = os.read(child, 1024 * 1024)
                                    if not chunk:
                                        break
                                    chunks.append(chunk)
                                if (
                                    hashlib.sha256(b"".join(chunks)).hexdigest()
                                    != entry["sha256"]
                                ):
                                    raise OwnershipRefusal("cleanup_file_bytes_changed")
                                checked(parent, name, entry)
                                os.unlink(name, dir_fd=parent)
                                deleted.append(rel)
                            finally:
                                os.close(child)
                    if os.listdir(parent):
                        raise OwnershipRefusal("late_unknown_cleanup_member_retained")

                remove(runtime_fd, "")
            finally:
                os.close(runtime_fd)
            if (
                identity(os.stat("runtime", dir_fd=fd, follow_symlinks=False))
                != r["completion_evidence"]["runtime_identity"]
            ):
                raise OwnershipRefusal("runtime_replaced")
            os.rmdir("runtime", dir_fd=fd)
            deleted.append("runtime")
            self.reopen_and_close(demo, r)
            self.release(demo, expected)
            if set(os.listdir(fd)) != {"receipt.json"}:
                raise OwnershipRefusal("unknown_member_retained")
            os.unlink("receipt.json", dir_fd=fd)
            if (
                identity(os.stat(demo, dir_fd=self.wfd, follow_symlinks=False))
                != r["workspace_identity"]
            ):
                raise OwnershipRefusal("workspace_replaced")
            os.rmdir(demo, dir_fd=self.wfd)
        except (OSError, OwnershipRefusal) as e:
            if deleted:
                if "receipt.json" not in os.listdir(fd):
                    write_at(fd, "receipt.json", r, True)
                self.persist_receipt(
                    demo,
                    r,
                    {
                        **r,
                        "state": "cleanup_incomplete",
                        "cleanup_deleted": deleted,
                        "cleanup_error": str(e),
                    },
                )
                raise OwnershipRefusal("partial_cleanup_retained") from e
            raise
        finally:
            os.close(fd)
        write_at(
            self.afd,
            demo + ".json",
            {
                "receipt": {**r, "state": "cleaned"},
                "receipt_hash": sha({**r, "state": "cleaned"}),
            },
        )


def demo_root() -> Path:
    return Path.home() / "Library/Application Support/Millrace/demo"
