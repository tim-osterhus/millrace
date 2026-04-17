from __future__ import annotations

import ast
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping, Sequence

PORTABLE_PROFILE: Final[str] = "portable"
MILLRACE_OPINIONATED_PROFILE: Final[str] = "millrace-opinionated"
DEFAULT_EVAL_CASE_ID: Final[str] = "package-smoke"
REQUIRED_SECTION_TITLES: Final[tuple[str, ...]] = (
    "Purpose",
    "Quick Start",
    "Operating Constraints",
    "Inputs This Skill Expects",
    "Output Contract",
    "Procedure",
    "Pitfalls And Gotchas",
    "Progressive Disclosure",
    "Verification Pattern",
)
SUPPORT_DIR_NAMES: Final[tuple[str, ...]] = ("references", "scripts", "evals")
SCRIPT_FILENAMES: Final[tuple[str, ...]] = (
    "_shared.py",
    "scaffold_skill.py",
    "lint_skill.py",
    "evaluate_skill.py",
)

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class SkillPackageSpec:
    profile: str
    asset_id: str | None = None
    description: str | None = None
    capability_type: str | None = None
    recommended_stages: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    title: str | None = None


def normalize_asset_id(name: str) -> str:
    slug = _SLUG_PATTERN.sub("-", name.lower()).strip("-")
    return slug or "skill"


def title_from_asset_id(asset_id: str) -> str:
    parts = [part for part in asset_id.replace("_", "-").split("-") if part]
    return " ".join(part.capitalize() for part in parts) if parts else "Skill"


def render_skill_markdown(spec: SkillPackageSpec) -> str:
    title = spec.title or title_from_asset_id(spec.asset_id or "skill")
    body = _render_skill_body(title)
    if spec.profile == PORTABLE_PROFILE:
        return body
    if spec.profile != MILLRACE_OPINIONATED_PROFILE:
        raise ValueError(f"unknown profile: {spec.profile}")
    if not spec.asset_id or not spec.description or not spec.capability_type:
        raise ValueError("opinionated profile requires asset_id, description, and capability_type")
    if not spec.forbidden_claims:
        raise ValueError("opinionated profile requires at least one forbidden_claim")

    frontmatter = [
        "---",
        f"asset_type: {_render_scalar('skill')}",
        f"asset_id: {_render_scalar(spec.asset_id)}",
        "version: 1",
        f"description: {_render_scalar(spec.description)}",
        "advisory_only: true",
        f"capability_type: {_render_scalar(spec.capability_type)}",
    ]
    if spec.recommended_stages:
        frontmatter.append("recommended_for_stages:")
        frontmatter.extend(f"  - {_render_scalar(stage)}" for stage in spec.recommended_stages)
    frontmatter.append("forbidden_claims:")
    frontmatter.extend(f"  - {_render_scalar(claim)}" for claim in spec.forbidden_claims)
    frontmatter.append("---")
    frontmatter.append("")
    frontmatter.append(body)
    return "\n".join(frontmatter).strip() + "\n"


def _render_skill_body(title: str) -> str:
    section_texts = {
        "Purpose": (
            "Define what this skill is for and what it intentionally does not own."
        ),
        "Quick Start": (
            "1. Fill in the package-specific metadata.\n"
            "2. Add support directories only if they are useful for this package.\n"
            "3. Run the local lint and evaluation commands when present."
        ),
        "Operating Constraints": (
            "- Keep the package truthful to its intended use.\n"
            "- Avoid inventing creator-specific defaults.\n"
            "- Stay local and deterministic."
        ),
        "Inputs This Skill Expects": (
            "- A package-specific skill identity.\n"
            "- Optional support directories when requested.\n"
            "- Fixture files when evaluating the package."
        ),
        "Output Contract": (
            "- Portable packages may remain `SKILL.md` only.\n"
            "- Opinionated packages must carry truthful frontmatter.\n"
            "- Optional support dirs should not be mandatory for linting."
        ),
        "Procedure": (
            "1. Scaffold the minimum package shape.\n"
            "2. Add optional support dirs only when needed.\n"
            "3. Lint the package locally.\n"
            "4. Evaluate against package-local or supplied fixtures."
        ),
        "Pitfalls And Gotchas": (
            "- Do not clone metadata from a different skill.\n"
            "- Do not force support dirs into every package.\n"
            "- Do not require a runtime registry to make the package usable."
        ),
        "Progressive Disclosure": (
            "Start with a portable `SKILL.md` only package.\n"
            "Add references, scripts, or evals only when that extra surface is justified.\n"
            "Use opinionated frontmatter only when the package needs to ship into Millrace."
        ),
        "Verification Pattern": (
            "Run lint first.\n"
            "Then run evaluation with package-local fixtures or explicit `--fixtures` paths.\n"
            "Keep the output deterministic so repeated runs produce the same result."
        ),
    }
    lines = [f"# {title}", ""]
    for section_title in REQUIRED_SECTION_TITLES:
        lines.append(f"## {section_title}")
        lines.append(section_texts[section_title])
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def scaffold_skill_package(
    destination: Path,
    spec: SkillPackageSpec,
    *,
    include: Sequence[str] = (),
    template_root: Path | None = None,
) -> None:
    root = destination.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    include_set = set(include)

    _write_text(root / "SKILL.md", render_skill_markdown(spec))

    if "references" in include_set:
        _write_text(root / "references" / "hybrid-format.md", render_reference_hybrid_format())
        _write_text(root / "references" / "donor-synthesis.md", render_reference_donor_synthesis())

    if "scripts" in include_set:
        source_root = template_root or Path(__file__).resolve().parent
        _copy_script_bundle(source_root=source_root, destination=root / "scripts")

    if "evals" in include_set:
        _write_text(root / "evals" / "skill_smoke_cases.json", render_package_smoke_cases(spec))


def validate_skill_package(package_root: Path) -> tuple[ValidationIssue, ...]:
    root = package_root.expanduser().resolve()
    issues: list[ValidationIssue] = []

    skill_path = root / "SKILL.md"
    if not skill_path.is_file():
        issues.append(ValidationIssue(path="SKILL.md", message="missing required file"))
        return tuple(issues)

    issues.extend(validate_skill_markdown(skill_path))
    issues.extend(_validate_optional_support_dirs(root))
    return tuple(sorted(issues, key=lambda issue: (issue.path, issue.message)))


def validate_skill_markdown(path: Path) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    raw = path.read_text(encoding="utf-8")

    if raw.startswith("---"):
        manifest, _body = _parse_markdown_asset(path)
        if manifest.get("asset_type") != "skill":
            issues.append(ValidationIssue(path="SKILL.md", message="asset_type must be skill"))
        if not isinstance(manifest.get("asset_id"), str) or not manifest.get("asset_id"):
            issues.append(ValidationIssue(path="SKILL.md", message="asset_id must be a non-empty string"))
        if manifest.get("version") != 1:
            issues.append(ValidationIssue(path="SKILL.md", message="version must be 1"))
        if not isinstance(manifest.get("description"), str) or not manifest.get("description"):
            issues.append(ValidationIssue(path="SKILL.md", message="description must be a non-empty string"))
        if manifest.get("advisory_only") is not True:
            issues.append(ValidationIssue(path="SKILL.md", message="advisory_only must be true"))
        if not isinstance(manifest.get("capability_type"), str) or not manifest.get("capability_type"):
            issues.append(ValidationIssue(path="SKILL.md", message="capability_type must be a non-empty string"))
        forbidden_claims = manifest.get("forbidden_claims")
        if not isinstance(forbidden_claims, list) or not forbidden_claims or not all(
            isinstance(claim, str) and claim for claim in forbidden_claims
        ):
            issues.append(ValidationIssue(path="SKILL.md", message="forbidden_claims must be a non-empty string list"))
        recommended = manifest.get("recommended_for_stages")
        if recommended is not None and (
            not isinstance(recommended, list)
            or not all(isinstance(stage, str) and stage for stage in recommended)
        ):
            issues.append(ValidationIssue(path="SKILL.md", message="recommended_for_stages must be a string list"))

    section_titles = _section_titles(raw)
    if section_titles != list(REQUIRED_SECTION_TITLES):
        issues.append(ValidationIssue(path="SKILL.md", message="skill body must contain the required section contract"))

    return tuple(issues)


def evaluate_skill_package(
    package_root: Path,
    *,
    fixture_paths: Sequence[Path] | None = None,
    case_id: str | None = None,
) -> tuple[tuple[str, tuple[ValidationIssue, ...]], ...]:
    root = package_root.expanduser().resolve()
    cases = load_fixture_cases(root, fixture_paths=fixture_paths)
    selected: list[tuple[str, tuple[ValidationIssue, ...]]] = []

    for case in cases:
        current_case_id = case.get("case_id")
        if case_id is not None and current_case_id != case_id:
            continue
        if isinstance(current_case_id, str) and current_case_id:
            selected.append((current_case_id, evaluate_case(root, case)))

    if case_id is not None and not selected:
        raise ValueError(f"unknown case_id: {case_id}")
    if not selected:
        raise ValueError("no fixture cases found")
    return tuple(selected)


def load_fixture_cases(
    package_root: Path,
    *,
    fixture_paths: Sequence[Path] | None = None,
) -> tuple[dict[str, Any], ...]:
    discovered_paths = _resolve_fixture_paths(package_root, fixture_paths=fixture_paths)
    cases: list[dict[str, Any]] = []
    for path in discovered_paths:
        cases.extend(load_cases_document(path))
    return tuple(cases)


def load_cases_document(path: Path) -> tuple[dict[str, Any], ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        cases = payload.get("cases", [])
    else:
        cases = payload
    if not isinstance(cases, list):
        raise ValueError(f"{path.name} must contain a list of cases")

    normalized: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError(f"{path.name} contains a non-object case entry")
        normalized.append(case)
    return tuple(normalized)


def evaluate_case(package_root: Path, case: Mapping[str, Any]) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    case_id = case.get("case_id")
    case_label = case_id if isinstance(case_id, str) and case_id else "<unknown>"

    required_paths = _string_list(case, "required_paths")
    forbidden_paths = _string_list(case, "forbidden_paths")
    required_skill_sections = _string_list(case, "required_skill_sections")
    file_contains = _file_contains(case)
    skill_manifest = _skill_manifest(case)

    for relative_path in required_paths:
        if not (package_root / relative_path).is_file():
            issues.append(ValidationIssue(path=relative_path, message=f"{case_label}: missing required file"))

    for relative_path in forbidden_paths:
        if (package_root / relative_path).exists():
            issues.append(ValidationIssue(path=relative_path, message=f"{case_label}: forbidden file is present"))

    if required_skill_sections:
        skill_path = package_root / "SKILL.md"
        if skill_path.is_file() and _section_titles(skill_path.read_text(encoding="utf-8")) != list(
            required_skill_sections
        ):
            issues.append(ValidationIssue(path="SKILL.md", message=f"{case_label}: skill sections do not match"))

    if skill_manifest:
        skill_path = package_root / "SKILL.md"
        if skill_path.is_file():
            asset_manifest, _body = _parse_markdown_asset(skill_path)
            for key, expected_value in skill_manifest.items():
                if asset_manifest.get(key) != expected_value:
                    issues.append(ValidationIssue(path="SKILL.md", message=f"{case_label}: expected {key}={expected_value!r}"))

    for relative_path, substrings in file_contains.items():
        text_path = package_root / relative_path
        if not text_path.is_file():
            issues.append(ValidationIssue(path=relative_path, message=f"{case_label}: missing file for substring check"))
            continue
        text = text_path.read_text(encoding="utf-8")
        for substring in substrings:
            if substring not in text:
                issues.append(ValidationIssue(path=relative_path, message=f"{case_label}: missing substring {substring!r}"))

    return tuple(issues)


def render_reference_hybrid_format() -> str:
    return (
        "# Hybrid Format\n\n"
        "This package supports two profiles:\n\n"
        "- `portable`: body-only markdown, no Millrace frontmatter required.\n"
        "- `millrace-opinionated`: body plus the current shipped Millrace skill frontmatter.\n\n"
        "Choose the smallest profile that still matches the package's intended use.\n"
    )


def render_reference_donor_synthesis() -> str:
    return (
        "# Donor Synthesis\n\n"
        "Use donor synthesis when you want to assemble a skill from proven source material.\n\n"
        "1. Keep the required section contract intact.\n"
        "2. Use package-specific metadata instead of cloning another package's identity.\n"
        "3. Re-run local lint and evaluation before shipping.\n"
    )


def render_package_smoke_cases(spec: SkillPackageSpec) -> str:
    case: dict[str, Any] = {
        "case_id": DEFAULT_EVAL_CASE_ID,
        "description": "Package-local smoke check for the scaffolded skill.",
        "required_paths": ["SKILL.md"],
        "required_skill_sections": list(REQUIRED_SECTION_TITLES),
    }
    if spec.profile == MILLRACE_OPINIONATED_PROFILE:
        if not spec.asset_id or not spec.description or not spec.capability_type:
            raise ValueError("opinionated profile requires asset_id, description, and capability_type")
        case["skill_manifest"] = {
            "asset_type": "skill",
            "asset_id": spec.asset_id,
            "version": 1,
            "description": spec.description,
            "advisory_only": True,
            "capability_type": spec.capability_type,
            "forbidden_claims": list(spec.forbidden_claims),
        }
        if spec.recommended_stages:
            case["skill_manifest"]["recommended_for_stages"] = list(spec.recommended_stages)
    return json.dumps({"cases": [case]}, indent=2) + "\n"


def _validate_optional_support_dirs(root: Path) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []

    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        for path in sorted(scripts_dir.glob("*.py")):
            issues.extend(
                validate_python_file(
                    path,
                    require_main_guard=path.name in {"scaffold_skill.py", "lint_skill.py", "evaluate_skill.py"},
                )
            )

    evals_dir = root / "evals"
    if evals_dir.is_dir():
        for path in sorted(evals_dir.glob("*.json")):
            issues.extend(validate_cases_file(path))

    return tuple(issues)


def validate_python_file(path: Path, *, require_main_guard: bool = True) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    source = path.read_text(encoding="utf-8")
    try:
        ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        issues.append(ValidationIssue(path=str(path), message=f"python syntax error: {exc.msg}"))
        return tuple(issues)

    if require_main_guard and "if __name__ == \"__main__\"" not in source and "if __name__ == '__main__'" not in source:
        issues.append(ValidationIssue(path=str(path), message="script must include a __main__ guard"))
    return tuple(issues)


def validate_cases_file(path: Path) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    for case in load_cases_document(path):
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            issues.append(ValidationIssue(path=str(path), message="case is missing case_id"))
    return tuple(issues)


def _resolve_fixture_paths(
    package_root: Path,
    *,
    fixture_paths: Sequence[Path] | None,
) -> tuple[Path, ...]:
    discovered: list[Path] = []
    if fixture_paths is None:
        evals_dir = package_root / "evals"
        if evals_dir.is_dir():
            discovered.extend(sorted(evals_dir.glob("*.json")))
    else:
        for raw_path in fixture_paths:
            path = raw_path.expanduser().resolve()
            if path.is_dir():
                discovered.extend(sorted(path.glob("*.json")))
            elif path.is_file():
                discovered.append(path)
    normalized = sorted({path.resolve() for path in discovered}, key=str)
    if not normalized:
        raise ValueError("no fixture files found")
    return tuple(normalized)


def _copy_script_bundle(*, source_root: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for filename in SCRIPT_FILENAMES:
        source = source_root / filename
        target = destination / filename
        if source.resolve() == target.resolve():
            continue
        shutil.copyfile(source, target)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _section_titles(text: str) -> list[str]:
    return [line.removeprefix("## ").strip() for line in text.splitlines() if line.startswith("## ")]


def _string_list(case: Mapping[str, Any], key: str) -> tuple[str, ...]:
    raw_value = case.get(key)
    if raw_value is None:
        return ()
    if not isinstance(raw_value, list):
        raise ValueError(f"case field {key} must be a list of strings")
    values: list[str] = []
    for item in raw_value:
        if not isinstance(item, str):
            raise ValueError(f"case field {key} must contain strings")
        values.append(item)
    return tuple(values)


def _file_contains(case: Mapping[str, Any]) -> Mapping[str, tuple[str, ...]]:
    raw_value = case.get("file_contains")
    if raw_value is None:
        return {}
    if not isinstance(raw_value, Mapping):
        raise ValueError("case field file_contains must be a mapping")

    normalized: dict[str, tuple[str, ...]] = {}
    for relative_path, substrings in raw_value.items():
        if not isinstance(relative_path, str):
            raise ValueError("case field file_contains keys must be strings")
        if not isinstance(substrings, list) or not all(isinstance(item, str) for item in substrings):
            raise ValueError("case field file_contains values must be string lists")
        normalized[relative_path] = tuple(substrings)
    return normalized


def _skill_manifest(case: Mapping[str, Any]) -> Mapping[str, Any]:
    raw_value = case.get("skill_manifest")
    if raw_value is None:
        return {}
    if not isinstance(raw_value, Mapping):
        raise ValueError("case field skill_manifest must be a mapping")
    return raw_value


def _parse_markdown_asset(path: Path) -> tuple[dict[str, object], str]:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if lines and lines[0].strip() == "---":
        frontmatter_text, body = _split_frontmatter(raw)
        return _parse_frontmatter_map(frontmatter_text), body

    return {"asset_type": "entrypoint", "stage": path.stem, "plane": path.parent.name}, raw.strip()


def _split_frontmatter(raw: str) -> tuple[str, str]:
    lines = raw.splitlines()
    if not lines:
        raise ValueError("asset file is empty")
    if lines[0].strip() != "---":
        raise ValueError("asset file is missing YAML frontmatter start marker")

    end_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        raise ValueError("asset file is missing YAML frontmatter end marker")

    frontmatter = "\n".join(lines[1:end_index])
    body = "\n".join(lines[end_index + 1 :]).strip()
    return frontmatter, body


def _parse_frontmatter_map(frontmatter: str) -> dict[str, object]:
    manifest: dict[str, object] = {}
    active_list_key: str | None = None

    for raw_line in frontmatter.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if raw_line.startswith("  - ") or raw_line.startswith("- "):
            if active_list_key is None:
                raise ValueError("frontmatter list item without key")
            item_raw = stripped[2:].strip()
            current = manifest.get(active_list_key)
            if not isinstance(current, list):
                raise ValueError("frontmatter list key malformed")
            current.append(_parse_scalar(item_raw))
            continue

        if ":" not in raw_line:
            raise ValueError("frontmatter line missing `:` separator")

        key_raw, value_raw = raw_line.split(":", 1)
        key = key_raw.strip()
        value = value_raw.strip()

        if not key:
            raise ValueError("frontmatter contains an empty key")

        if value == "":
            manifest[key] = []
            active_list_key = key
        else:
            manifest[key] = _parse_scalar(value)
            active_list_key = None

    return manifest


def _parse_scalar(value: str) -> object:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False

    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item.strip()) for item in inner.split(",")]

    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value[1:-1]
        return parsed if isinstance(parsed, str) else value[1:-1]

    if re.fullmatch(r"-?\d+", value):
        return int(value)

    return value


def _render_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    raise TypeError(f"unsupported scalar type: {type(value)!r}")
