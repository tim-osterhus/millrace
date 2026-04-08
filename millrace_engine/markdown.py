"""Markdown parsing and atomic write helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from tempfile import NamedTemporaryFile
import os
import re

from .contracts import CARD_HEADING_RE, TaskCard


@dataclass(frozen=True, slots=True)
class TaskStoreDocument:
    """Parsed markdown store with preserved preamble."""

    preamble: str
    cards: list[TaskCard]


def parse_task_store(text: str, *, source_file: Path | None = None) -> TaskStoreDocument:
    """Parse a markdown task store and preserve the non-card preamble."""

    normalized = text.replace("\r\n", "\n")
    lines = normalized.splitlines()
    starts = [index for index, line in enumerate(lines) if CARD_HEADING_RE.match(line.strip())]

    if not starts:
        return TaskStoreDocument(preamble=normalized.rstrip("\n"), cards=[])

    preamble = "\n".join(lines[: starts[0]]).rstrip("\n")
    cards: list[TaskCard] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        raw_markdown = "\n".join(lines[start:end]).rstrip("\n")
        cards.append(TaskCard.from_markdown(raw_markdown, source_file=source_file))
    return TaskStoreDocument(preamble=preamble, cards=cards)


def parse_task_cards(text: str, *, source_file: Path | None = None) -> list[TaskCard]:
    """Parse all task cards from markdown text."""

    return parse_task_store(text, source_file=source_file).cards


def render_task_store(document: TaskStoreDocument) -> str:
    """Render a parsed task store back to markdown."""

    sections: list[str] = []
    preamble = document.preamble.rstrip("\n")
    if preamble:
        sections.append(preamble)
    if document.cards:
        sections.append("\n\n".join(card.render_markdown() for card in document.cards))
    rendered = "\n\n".join(section for section in sections if section)
    if not rendered:
        return ""
    return rendered.rstrip("\n") + "\n"


def insert_after_preamble(existing_text: str, block: str) -> str:
    """Insert a markdown block before the first task-card entry."""

    normalized = existing_text.replace("\r\n", "\n").rstrip("\n")
    block_text = block.rstrip("\n")
    match = re.search(r"^##\s+", normalized, flags=re.MULTILINE)
    if not match:
        if not normalized:
            return block_text + "\n"
        return normalized + "\n\n" + block_text + "\n"

    preamble = normalized[: match.start()].rstrip("\n")
    remainder = normalized[match.start() :].strip("\n")
    sections = [preamble, block_text]
    if remainder:
        sections.append(remainder)
    return "\n\n".join(section for section in sections if section) + "\n"


def append_markdown_block(existing_text: str, block: str) -> str:
    """Append a markdown block with predictable spacing."""

    normalized = existing_text.replace("\r\n", "\n").rstrip("\n")
    block_text = block.rstrip("\n")
    if not normalized:
        return block_text + "\n"
    return normalized + "\n\n" + block_text + "\n"


def write_text_atomic(path: Path, text: str) -> None:
    """Atomically rewrite a utf-8 text file."""

    name = path.name
    if len(name) > 32:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
        name = f"{name[:16]}.{digest}"
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)
