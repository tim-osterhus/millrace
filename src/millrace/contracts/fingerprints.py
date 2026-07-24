"""Authority fingerprint type aliases and domain separation constants."""

from __future__ import annotations

from typing import TypeAlias

AuthorityFingerprint: TypeAlias = str

AUTHORITY_FINGERPRINT_DOMAIN_PREFIX = b"millrace-authority-v1\0"

__all__ = (
    "AUTHORITY_FINGERPRINT_DOMAIN_PREFIX",
    "AuthorityFingerprint",
)
