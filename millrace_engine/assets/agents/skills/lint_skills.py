#!/usr/bin/env python3
"""
Minimal Millrace skill linter.

Goal: prevent drift (broken indexing, missing sections, inconsistent example format).

No third-party deps. Run from repo root:
  python3 agents/skills/lint_skills.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Issue:
  path: Path
  message: str


def _read_text(path: Path) -> str:
  return path.read_text(encoding="utf-8")


def _repo_root() -> Path:
  # .../agents/skills/lint_skills.py -> .../ (repo root)
  return Path(__file__).resolve().parents[2]


def _skills_dir() -> Path:
  return Path(__file__).resolve().parent


def _extract_frontmatter(md: str) -> str | None:
  # Very small YAML frontmatter extractor (no YAML parsing).
  if not md.startswith("---\n"):
    return None
  end = md.find("\n---", 4)
  if end == -1:
    return None
  # Include the trailing newline after '---' if present.
  end2 = md.find("\n", end + 1)
  if end2 == -1:
    end2 = end + 4
  return md[4:end].strip("\n")


def _lint_skill_md(skill_md: Path) -> list[Issue]:
  issues: list[Issue] = []
  text = _read_text(skill_md)

  fm = _extract_frontmatter(text)
  if fm is None:
    issues.append(Issue(skill_md, "Missing or malformed YAML frontmatter (expected leading --- ... ---)."))
    return issues

  if not re.search(r"(?m)^\s*name\s*:\s*[a-z0-9-]+\s*$", fm):
    issues.append(Issue(skill_md, "Missing/invalid frontmatter 'name:' (expected kebab-case)."))
  if not re.search(r"(?m)^\s*description\s*:\s*>", fm) and not re.search(r"(?m)^\s*description\s*:", fm):
    issues.append(Issue(skill_md, "Missing frontmatter 'description:'."))

  # Compatibility metadata contract (simple presence checks; no YAML parsing).
  if not re.search(r"(?m)^\s*compatibility\s*:\s*$", fm):
    issues.append(Issue(skill_md, "Missing frontmatter 'compatibility:' block."))
  else:
    if not re.search(r"(?m)^\s*runners\s*:\s*", fm):
      issues.append(Issue(skill_md, "Missing frontmatter 'compatibility.runners'."))
    if not re.search(r"(?m)^\s*tools\s*:\s*", fm):
      issues.append(Issue(skill_md, "Missing frontmatter 'compatibility.tools'."))
    if not re.search(r"(?m)^\s*offline_ok\s*:\s*(true|false)\s*$", fm):
      issues.append(Issue(skill_md, "Missing/invalid frontmatter 'compatibility.offline_ok' (expected true|false)."))

  # Section contract checks (look for stable strings so agents can skim deterministically).
  required_snippets = [
    "## Purpose",
    "Use when (triggers):",
    "Do NOT use when",
    "## Inputs this Skill expects",
    "## Output contract",
    "## Procedure",
    "Definition of DONE",
  ]
  for s in required_snippets:
    if s not in text:
      issues.append(Issue(skill_md, f"Missing required section/snippet: {s!r}"))

  # Ban brittle line-number references.
  if re.search(r"EXAMPLES\.md:\d", text):
    issues.append(Issue(skill_md, "Contains brittle line-number reference (use Example IDs instead)."))

  return issues


def _lint_examples_md(examples_md: Path) -> list[Issue]:
  issues: list[Issue] = []
  text = _read_text(examples_md)
  lines = text.splitlines()

  # Split into example blocks by heading.
  blocks: list[tuple[str, list[str]]] = []
  current_id = ""
  current_lines: list[str] = []

  for line in lines:
    m = re.match(r"^##\s+(EX-\d{4}-\d{2}-\d{2}-\d{2})\b", line.strip())
    if m:
      if current_id:
        blocks.append((current_id, current_lines))
      current_id = m.group(1)
      current_lines = [line]
    else:
      if current_id:
        current_lines.append(line)

  if current_id:
    blocks.append((current_id, current_lines))

  if not blocks:
    issues.append(Issue(examples_md, "No examples found (expected headings like '## EX-YYYY-MM-DD-NN: ...')."))
    return issues

  seen: set[str] = set()
  last_id: str | None = None
  for ex_id, ex_lines in blocks:
    if ex_id in seen:
      issues.append(Issue(examples_md, f"Duplicate Example ID: {ex_id}"))
    seen.add(ex_id)

    # Optional-but-useful ordering check (enforced): flags out-of-order IDs to keep EXAMPLES.md append-only.
    if last_id is not None and ex_id < last_id:
      issues.append(Issue(examples_md, f"Example IDs out of order (append-only violated?): {ex_id} appears after {last_id}"))
    last_id = ex_id

    block_text = "\n".join(ex_lines)
    required_fields = [
      "**Trigger phrases**",
      "**Cause**",
      "**Fix**",
      "**Prevention**",
    ]
    for rf in required_fields:
      if rf not in block_text:
        issues.append(Issue(examples_md, f"{ex_id}: missing required field: {rf}"))

  return issues


def _lint_index(skills_root: Path, skill_dirs: list[Path], skills_index: Path) -> list[Issue]:
  issues: list[Issue] = []
  idx = _read_text(skills_index)

  # Collect all linked SKILL.md paths in the index.
  linked: set[str] = set()
  for m in re.finditer(r"\]\((\./[^)]+/SKILL\.md)\)", idx):
    linked.add(m.group(1).lstrip("./"))

  for d in skill_dirs:
    expected = f"{d.name}/SKILL.md"
    if expected not in linked:
      issues.append(Issue(skills_index, f"Skill folder missing from skills_index.md: {expected}"))

  return issues


def main() -> int:
  repo = _repo_root()
  skills_root = _skills_dir()
  skills_index = skills_root / "skills_index.md"

  if not skills_index.exists():
    print(f"ERROR: missing {skills_index}", file=sys.stderr)
    return 2

  skill_dirs = sorted([p for p in skills_root.iterdir() if p.is_dir() and not p.name.startswith("_")])
  skill_dirs = [p for p in skill_dirs if (p / "SKILL.md").exists()]

  issues: list[Issue] = []
  issues.extend(_lint_index(skills_root, skill_dirs, skills_index))

  for d in skill_dirs:
    skill_md = d / "SKILL.md"
    examples_md = d / "EXAMPLES.md"

    if not examples_md.exists():
      issues.append(Issue(examples_md, "Missing EXAMPLES.md"))
    issues.extend(_lint_skill_md(skill_md))
    if examples_md.exists():
      issues.extend(_lint_examples_md(examples_md))

  if issues:
    print("Skill lint failed:\n", file=sys.stderr)
    for i in issues:
      rel = i.path
      try:
        rel = i.path.relative_to(repo)
      except Exception:
        pass
      print(f"- {rel}: {i.message}", file=sys.stderr)
    return 1

  print("OK: skills lint passed")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
