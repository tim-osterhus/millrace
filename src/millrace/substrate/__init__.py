"""Public substrate storage facade."""

from millrace.substrate.cas import ContentAddressedByteStore, storage_digest_for_bytes
from millrace.substrate.errors import (
    CasDigestMismatch,
    CasObjectKindMismatch,
    CasObjectNotFound,
    InvalidCasDigest,
    InvalidCasObject,
    StorageIntegrityError,
    StoreNotInitialized,
    SubstrateError,
    UnsupportedCodec,
    UnsupportedRecordKind,
    UnsupportedSchemaVersion,
    UnsupportedStoreSchemaVersion,
)
from millrace.substrate.sqlite import SQLiteRuntimeStore, StoreSchemaMetadata

__all__ = (
    "CasDigestMismatch",
    "CasObjectKindMismatch",
    "CasObjectNotFound",
    "ContentAddressedByteStore",
    "InvalidCasDigest",
    "InvalidCasObject",
    "SQLiteRuntimeStore",
    "StoreNotInitialized",
    "StoreSchemaMetadata",
    "StorageIntegrityError",
    "SubstrateError",
    "UnsupportedCodec",
    "UnsupportedRecordKind",
    "UnsupportedSchemaVersion",
    "UnsupportedStoreSchemaVersion",
    "storage_digest_for_bytes",
)
