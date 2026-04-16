"""Stable public facade for mailbox command helpers."""

from __future__ import annotations

from millrace_ai.workspace.mailbox import (
    ArchiveDisposition,
    ClaimedMailboxCommand,
    MailboxDrainResult,
    archive_claimed_mailbox_command,
    claim_next_mailbox_command,
    drain_incoming_mailbox_commands,
    read_pending_mailbox_commands,
    write_mailbox_command,
)

__all__ = [
    "ArchiveDisposition",
    "ClaimedMailboxCommand",
    "MailboxDrainResult",
    "archive_claimed_mailbox_command",
    "claim_next_mailbox_command",
    "drain_incoming_mailbox_commands",
    "read_pending_mailbox_commands",
    "write_mailbox_command",
]
