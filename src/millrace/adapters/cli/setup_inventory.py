"""Read-only owner observations; no executable discovery or provider imports."""

from __future__ import annotations

import base64
import re
import sqlite3
from dataclasses import dataclass, replace
from hashlib import sha256
from importlib import metadata
from pathlib import Path, PurePosixPath

from millrace.adapters.cli.setup_preflight import ContextPreflight, inspect_context
from millrace.contracts.setup import (
    ComponentPin,
    RuntimeIdentity,
    SetupRequest,
    SetupSelection,
    digest,
)

# Compatibility, not publication authentication. New versions require review.
SUPPORTED_COMPONENTS = {
    "millrace-ai": (
        (
            "0.22.3",
            "0.22.4.dev0+s01",
            "0.22.4.dev0+s02",
            "0.22.4.dev0+s02mac01",
            "0.22.4.dev6+pi.local01",
            "0.22.4",
        ),
        "setup.v1/store.11/plan.18",
    ),
    "millrace-plus": (
        (
            "0.22.3",
            "0.22.4.dev0+s01",
            "0.22.4.dev0+s02",
            "0.22.4.dev0+s02mac01",
            "0.22.4.dev5+pi.local01",
            "0.22.4",
        ),
        "workflow-package.v1",
    ),
    "millrace": (
        (
            "0.22.3",
            "0.22.4.dev0+s01",
            "0.22.4.dev0+s02",
            "0.22.4.dev0+s02mac01",
            "0.22.4",
        ),
        "dependency-only.v1",
    ),
    "millforge": (("0.1.1",), "runner.0.1.1"),
}
_PACKAGE_ROOTS = {
    "millrace-ai": "millrace",
    "millrace-plus": "millrace_workflow_package",
    "millrace": None,
    "millforge": "millforge",
}


@dataclass(frozen=True, slots=True)
class InstalledComponent:
    pin: ComponentPin
    disposition: str
    installed_bytes_digest: str | None
    command_disposition: str | None = None
    loaded_origin_disposition: str | None = None


def installed_component(name: str) -> InstalledComponent:
    """Correlate declared installed bytes; RECORD is not a wheel digest or trust."""
    versions, contract = SUPPORTED_COMPONENTS[name]
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return InstalledComponent(
            ComponentPin(name, "unavailable", None, None),
            "missing",
            None,
            "distribution_missing" if name == "millrace-ai" else None,
            "unverifiable" if name == "millrace-ai" else None,
        )
    command, origin = (
        _command_observations(dist) if name == "millrace-ai" else (None, None)
    )
    version = dist.metadata["Version"]
    # "unavailable" is an explicit wire sentinel, not a guessed package version.
    # Do not truncate an identity or expose arbitrary malformed metadata bytes.
    if (
        not isinstance(version, str)
        or re.fullmatch(r"[A-Za-z0-9.+!_-]{1,512}", version) is None
    ):
        return InstalledComponent(
            ComponentPin(name, "unavailable", None, None),
            "version_unverifiable",
            None,
            command,
            origin,
        )
    pin = ComponentPin(name, version, None, contract if version in versions else None)

    def observed(
        disposition: str, byte_digest: str | None = None
    ) -> InstalledComponent:
        return InstalledComponent(pin, disposition, byte_digest, command, origin)

    if version not in versions:
        return observed("incompatible")
    try:
        files = dist.files
        if not files:
            return observed("unverifiable")
        root = Path(str(dist.locate_file(""))).resolve()
        rows: list[tuple[str, str]] = []
        content_count = 0
        for item in sorted(files, key=str):
            path = PurePosixPath(str(item))
            # Never follow installer script paths outside the distribution root,
            # credential/config paths, symlinks or arbitrary RECORD additions.
            if path.is_absolute() or ".." in path.parts:
                continue
            allowed_root = _PACKAGE_ROOTS[name]
            is_package = allowed_root is not None and path.parts[0] == allowed_root
            is_metadata = path.parts[0].endswith(".dist-info") and path.name in {
                "METADATA",
                "WHEEL",
                "entry_points.txt",
            }
            if not (is_package or is_metadata) or "__pycache__" in path.parts:
                continue
            target = Path(str(dist.locate_file(item)))
            if target.is_symlink() or not target.resolve().is_relative_to(root):
                return observed("unverifiable")
            if any(part.is_symlink() for part in target.parents if part != root):
                return observed("unverifiable")
            data = target.read_bytes()
            if len(data) > 16 * 1024 * 1024:
                return observed("unverifiable")
            value = sha256(data).digest()
            if (
                item.hash is None
                or item.hash.mode != "sha256"
                or (
                    base64.urlsafe_b64encode(value).rstrip(b"=").decode()
                    != item.hash.value
                )
            ):
                return observed("installed_bytes_mismatch")
            rows.append((str(path), value.hex()))
            content_count += int(is_package)
        if not rows or (_PACKAGE_ROOTS[name] is not None and not content_count):
            return observed("unverifiable")
        return observed("installed_bytes_correlated", digest(rows))
    except (OSError, ValueError, TypeError):
        return observed("unverifiable")


def _command_observations(dist: metadata.Distribution) -> tuple[str, str]:
    """Independent installer declaration and loaded-path facts; neither is readiness."""
    try:
        entries = [
            e
            for e in dist.entry_points
            if e.group == "console_scripts" and e.name == "millrace"
        ]
        declaration = (
            "missing"
            if not entries
            else (
                "declared"
                if len(entries) == 1
                and entries[0].value == "millrace.adapters.cli.main:cli"
                else "incompatible"
            )
        )
    except (OSError, ValueError, TypeError, KeyError):
        declaration = "unverifiable"
    try:
        from millrace.adapters.cli import main

        loaded = Path(main.__file__).resolve(strict=True)
        owned = Path(str(dist.locate_file("millrace/adapters/cli/main.py")))
        origin = (
            "installed_path_match"
            if (
                owned.is_file()
                and not owned.is_symlink()
                and loaded == owned.resolve(strict=True)
            )
            else "source_overlay"
        )
    except (OSError, ValueError, TypeError):
        origin = "unverifiable"
    return declaration, origin


@dataclass(frozen=True, slots=True)
class SelectionObservation:
    selection: SetupSelection
    plan_disposition: str
    installed: tuple[InstalledComponent, ...]
    demo_package_disposition: str
    demo_command_disposition: str = "unused"
    context: ContextPreflight | None = None


def observe_selection(request: SetupRequest) -> SelectionObservation:
    components = tuple(installed_component(name) for name in SUPPORTED_COMPONENTS)
    selection = SetupSelection(
        action_kind=request.action_kind,
        component_pins=tuple(c.pin for c in components),
        declared_next_action=(
            "millrace demo"
            if request.action_kind == "demo"
            else "millrace --bounded plan show"
        ),
    )
    if request.action_kind == "demo":
        from millrace.adapters.cli.demo_inventory import (
            installed_identity,
            planned_package,
        )
        from millrace.adapters.cli.demo_workspace import demo_root
        from millrace.contracts.demo import PLAN_FINGERPRINT
        from millrace.contracts.setup import DemoCommandIdentity

        try:
            identity = installed_identity()
            package = planned_package()
            components = tuple(
                replace(
                    c,
                    pin=replace(
                        c.pin,
                        artifact_sha256=identity[
                            "core_artifact"
                            if c.pin.distribution == "millrace-ai"
                            else "plus_artifact"
                        ],
                    ),
                    disposition="artifact_correlated",
                )
                if c.pin.distribution in {"millrace-ai", "millrace-plus"}
                else c
                for c in components
            )
            from millrace.adapters.cli.setup_receipts import secure_directory

            root_path = demo_root()
            existing = root_path
            while not existing.exists():
                if existing.is_symlink():
                    raise ValueError("unsafe_demo_root")
                existing = existing.parent
            import os

            descriptor = secure_directory(existing)
            os.close(descriptor)
            if root_path.exists():
                from millrace.adapters.cli.demo_workspace import CommandHome

                home = CommandHome(root_path)
                home.current_root()
                home.close()
            root = str(root_path)
            selection = replace(
                selection,
                component_pins=tuple(
                    c.pin
                    for c in components
                    if c.pin.distribution in {"millrace-ai", "millrace-plus"}
                ),
                workspace=root,
                package_id=package["package_id"],
                package_version=package["package_version"],
                source_digest=package["source_digest"],
                import_record_digest=package["import_record_digest"],
                manifest_digest=package["manifest_digest"],
                plan_fingerprint=PLAN_FINGERPRINT,
                local_config_digest=digest(
                    {"profile": "owned-demo-v1", "root": root, "installation": identity}
                ),
                runtime_identity=RuntimeIdentity(
                    "planned:owned-demo-v1", root, disposition="planned_isolated"
                ),
                demo_command_identity=DemoCommandIdentity(
                    "millrace-ai",
                    identity["core_version"],
                    identity["core_artifact"],
                    "1",
                ),
            )
            return SelectionObservation(
                selection, "demo_observed", components, "approved_demo", "qualified"
            )
        except (ValueError, OSError, metadata.PackageNotFoundError):
            return SelectionObservation(
                selection, "demo_unavailable", components, "unverifiable", "unqualified"
            )
    return _observe_recipe(request, selection, components)


def _observe_recipe(
    request: SetupRequest,
    selection: SetupSelection,
    components: tuple[InstalledComponent, ...],
) -> SelectionObservation:
    from millrace.adapters.cli.context import workspace_paths
    from millrace.contracts.compiled_plan import verify_authority_fingerprint
    from millrace.operator.packages import project_current_workflow_package
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.errors import (
        StoreIdentityMismatch,
        StoreNotInitialized,
        StoreSchemaUpgradeRequired,
        SubstrateError,
    )
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    paths = workspace_paths(request)
    selection = replace(
        selection,
        workspace=str(paths.workspace_path),
        # Safe selected CLI paths only; no runner configuration or credentials.
        local_config_digest=digest(
            {
                "domain": "Core.setup.local-path-config.v1",
                "workspace": str(paths.workspace_path),
                "db": str(paths.db_path),
                "cas": str(paths.cas_path),
                "management_mode": request.management_mode,
            }
        ),
    )
    store = None
    try:
        if not paths.cas_path.is_dir():
            return SelectionObservation(
                selection, "workspace_missing", components, "unused"
            )
        store = SQLiteRuntimeStore.open_readonly(
            paths.db_path, workspace_path=paths.workspace_path, cas_path=paths.cas_path
        )
        cas = ContentAddressedByteStore(paths.cas_path)
        with store.read_transaction():
            identity = store.control_identity()
            selection = replace(
                selection,
                runtime_identity=RuntimeIdentity(
                    str(identity["workspace_id"]), str(paths.workspace_path)
                ),
            )
            state = store.load_runtime_state(cas)
            fingerprint = request.plan_fingerprint
            if fingerprint is None and state.default_plan_ref is not None:
                fingerprint = str(state.default_plan_ref.authority_fingerprint)
            admitted = state.admitted_plans.get(fingerprint) if fingerprint else None
            if admitted is None:
                return SelectionObservation(
                    selection, "plan_not_selected", components, "unused"
                )
            plan = admitted.selected_plan
            if not verify_authority_fingerprint(
                plan, admitted.plan_ref.authority_fingerprint
            ):
                return SelectionObservation(
                    selection, "plan_identity_mismatch", components, "unused"
                )
            selection = replace(selection, plan_fingerprint=fingerprint)
            pin = plan.workflow_package_pin
            if pin is None:
                return SelectionObservation(
                    selection, "package_not_selected", components, "unused"
                )
            selection = replace(
                selection,
                package_id=pin.package_id,
                package_version=pin.package_version,
            )
            package = project_current_workflow_package(
                store, cas, pin.package_id, pin.package_version
            )
            if package is None or not package.selectable:
                return SelectionObservation(
                    selection, "package_unavailable", components, "unused"
                )
            matched = (
                package.package_format_version == pin.package_format_version
                and all(
                    any(
                        a.asset_id == p.asset_id
                        and a.content_digest == p.content_digest
                        for a in package.assets
                    )
                    for p in pin.selected_asset_pins
                )
                and all(
                    any(
                        d.get("package_id") == p.package_id
                        and d.get("package_version") == p.package_version
                        and d.get("package_format_version") == p.package_format_version
                        for d in package.dependencies
                    )
                    for p in pin.selected_dependency_pins
                )
            )
            selection = replace(
                selection,
                source_digest=package.provenance.source_digest,
                import_record_digest=package.provenance.import_record_digest,
                manifest_digest=package.manifest_digest,
            )
            context = inspect_context(plan, paths, cas) if matched else None
            selection = replace(
                selection,
                context_digest=context.context_digest if context is not None else None,
            )
            return SelectionObservation(
                selection,
                "observed" if matched else "package_plan_mismatch",
                components,
                "unused",
                context=context,
            )
    except StoreSchemaUpgradeRequired:
        reason = "workspace_schema_incompatible"
    except StoreIdentityMismatch:
        reason = "workspace_identity_mismatch"
    except StoreNotInitialized:
        reason = (
            "workspace_unverifiable" if paths.db_path.exists() else "workspace_missing"
        )
    except (SubstrateError, sqlite3.Error, ValueError, OSError):
        reason = "workspace_unverifiable"
    finally:
        if store is not None:
            store.close()
    return SelectionObservation(selection, reason, components, "unused")
