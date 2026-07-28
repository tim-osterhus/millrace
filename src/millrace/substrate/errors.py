"""Typed substrate persistence errors."""

from __future__ import annotations


class SubstrateError(Exception):
    """Base class for substrate storage and durable-codec failures."""


class InvalidCasDigest(SubstrateError, ValueError):
    """Raised when a CAS digest does not use the supported shape."""


class CasObjectNotFound(SubstrateError, FileNotFoundError):
    """Raised when a requested CAS object is absent."""


class CasDigestMismatch(SubstrateError):
    """Raised when stored bytes do not match the requested digest."""


class InvalidCasObject(SubstrateError, ValueError):
    """Raised when a CAS object envelope or payload is malformed."""


class UnsupportedRecordKind(SubstrateError, ValueError):
    """Raised when a durable record kind is not supported."""


class UnsupportedSchemaVersion(SubstrateError, ValueError):
    """Raised when a durable schema version is not supported."""


class StoreNotInitialized(SubstrateError, RuntimeError):
    """Raised when a SQLite store lacks the fresh-store marker."""


class UnsupportedStoreSchemaVersion(UnsupportedSchemaVersion):
    """Raised when a SQLite store schema version is not supported."""


class StoreSchemaUpgradeRequired(UnsupportedStoreSchemaVersion):
    """Raised only when a recognized prior store requires an explicit upgrade."""


class UnsupportedCodec(SubstrateError, ValueError):
    """Raised when a CAS object uses an unsupported codec."""


class CasObjectKindMismatch(SubstrateError, ValueError):
    """Raised when a CAS object has a different kind than its reference expects."""


class StorageIntegrityError(SubstrateError, RuntimeError):
    """Raised when durable store rows and CAS references violate invariants."""


__all__ = (
    "CasDigestMismatch",
    "CasObjectKindMismatch",
    "CasObjectNotFound",
    "InvalidCasDigest",
    "InvalidCasObject",
    "StoreNotInitialized",
    "StoreSchemaUpgradeRequired",
    "StorageIntegrityError",
    "SubstrateError",
    "UnsupportedCodec",
    "UnsupportedRecordKind",
    "UnsupportedSchemaVersion",
    "UnsupportedStoreSchemaVersion",
)
