"""Exact macOS process birth observation; never sends process-directed signals."""

from __future__ import annotations

import ctypes
import errno
import os
import socket
import sys
from typing import Any


class _BsdInfo(ctypes.Structure):
    # macOS SDK sys/proc_info.h: PROC_PIDTBSDINFO (3), proc_bsdinfo.
    _fields_ = (
        [
            (name, ctypes.c_uint32)
            for name in (
                "flags",
                "status",
                "xstatus",
                "pid",
                "ppid",
                "uid",
                "gid",
                "ruid",
                "rgid",
                "svuid",
                "svgid",
                "reserved",
            )
        ]
        + [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
        + [
            (name, ctypes.c_uint32)
            for name in ("nfiles", "pgid", "pjobc", "tdev", "tpgid")
        ]
        + [
            ("nice", ctypes.c_int32),
            ("start_sec", ctypes.c_uint64),
            ("start_usec", ctypes.c_uint64),
        ]
    )


class _Timeval(ctypes.Structure):
    _fields_ = [("seconds", ctypes.c_long), ("microseconds", ctypes.c_int)]


def process_identity(pid: int) -> dict[str, Any]:
    """ESRCH alone is absence; unsupported, denied or partial observations unknown."""
    if sys.platform != "darwin" or type(pid) is not int or pid <= 0:
        return {"status": "unknown"}
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        libc.sysctlbyname.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        libc.sysctlbyname.restype = ctypes.c_int
        boot = _Timeval()
        size = ctypes.c_size_t(ctypes.sizeof(boot))
        if (
            libc.sysctlbyname(
                b"kern.boottime", ctypes.byref(boot), ctypes.byref(size), None, 0
            )
            != 0
            or size.value != ctypes.sizeof(boot)
            or boot.seconds <= 0
        ):
            return {"status": "unknown"}
        libproc.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        libproc.proc_pidinfo.restype = ctypes.c_int
        value = _BsdInfo()
        ctypes.set_errno(0)
        count = libproc.proc_pidinfo(
            pid, 3, 0, ctypes.byref(value), ctypes.sizeof(value)
        )
        error = ctypes.get_errno()
        if count == 0 and error == errno.ESRCH:
            return {"status": "not_live"}
        if count != ctypes.sizeof(value) or value.pid != pid or value.start_sec <= 0:
            return {"status": "unknown"}
        return {
            "status": "live",
            "pid": pid,
            "uid": value.uid,
            "birth": [value.start_sec, value.start_usec],
            "boot": [boot.seconds, boot.microseconds],
        }
    except (OSError, AttributeError, ValueError):
        return {"status": "unknown"}


def observe_process(expected: dict[str, Any]) -> str:
    observed = process_identity(expected["pid"])
    if observed["status"] == "not_live":
        return "not_live"
    if observed["status"] != "live":
        return "unknown"
    if observed == expected:
        return "live"
    # A differing boot or exact birth establishes absence of the old process.
    if observed["boot"] != expected["boot"] or observed["birth"] != expected["birth"]:
        return "not_live"
    return "unknown"


def same_uid_peer(connection: socket.socket) -> bool:
    if sys.platform != "darwin":
        return False
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        libc.getpeereid.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ]
        libc.getpeereid.restype = ctypes.c_int
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        return (
            libc.getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid))
            == 0
            and uid.value == os.geteuid()
        )
    except (OSError, AttributeError):
        return False
