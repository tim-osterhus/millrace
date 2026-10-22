"""Strict command graph validation and coherent control snapshots."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .execution import (
    VALIDATION_ID,
    VALIDATION_ROOT,
    AdapterConfiguration,
    AdapterResolver,
    InvocationRequest,
    LaunchPlan,
    SeamInput,
    _adapter,
    _admit,
    _object,
)
from .records import (
    Graph,
    Node,
    Plane,
    RouterError,
    Seam,
    TerminalDependency,
    action,
    common_settings,
    fields,
    identifier,
    mapping,
    number,
    relative,
    resolved,
    strict_json,
    strings,
    text,
    within,
)


def _nodes(
    value: object, start: str, bindings: dict[str, str], plane_id: str
) -> dict[str, Node]:
    nodes: dict[str, Node] = {}
    for name, value in mapping(value).items():
        identifier(name)
        item = fields(
            value,
            {"invoke", "result", "outcomes", "on", "on_error", "on_unknown"},
            {"requires_terminal"},
        )
        invoke = fields(
            item["invoke"],
            set(),
            {"binding", "timeout_seconds", "config", "cwd", "inputs"},
        )
        settings = common_settings(
            {
                key: invoke[key]
                for key in ("binding", "timeout_seconds")
                if key in invoke
            },
            location=f"planes.{plane_id}.nodes.{name}.invoke",
        )
        binding = cast(str | None, settings.get("binding"))
        if (binding is not None and binding not in bindings) or item[
            "result"
        ] != "json-v1":
            raise RouterError("invalid_contract", f"planes.{plane_id}.nodes.{name}")
        outcomes = strings(item["outcomes"])
        if not outcomes or len(set(outcomes)) != len(outcomes):
            raise RouterError("invalid_outcomes")
        for outcome in outcomes:
            identifier(outcome)
        routes = {key: action(route) for key, route in mapping(item["on"]).items()}
        if set(routes) != set(outcomes):
            raise RouterError("invalid_routes", f"planes.{plane_id}.nodes.{name}.on")
        dependencies: list[TerminalDependency] = []
        raw_dependencies = item.get("requires_terminal", [])
        if not isinstance(raw_dependencies, list):
            raise RouterError("invalid_dependency")
        seen: set[str] = set()
        for raw in raw_dependencies:
            dependency = fields(raw, {"plane", "status"})
            producer = identifier(dependency["plane"])
            status = text(dependency["status"])
            if (
                producer == plane_id
                or producer in seen
                or status not in {"succeeded", "failed", "cancelled"}
            ):
                raise RouterError("invalid_dependency")
            seen.add(producer)
            dependencies.append(TerminalDependency(producer, status))
        nodes[name] = Node(
            binding,
            relative(invoke.get("cwd", ".")),
            cast(float | None, settings.get("timeout_seconds")),
            tuple(
                relative(v, owner_forbidden=True)
                for v in strings(invoke.get("inputs", []))
            ),
            outcomes,
            routes,
            action(item["on_error"]),
            action(item["on_unknown"]),
            mapping(invoke.get("config", {})),
            tuple(dependencies),
        )
    if not nodes or start not in nodes:
        raise RouterError("invalid_start")
    edges: dict[str, set[str]] = {}
    for name, node in nodes.items():
        targets: set[str] = set()
        for route in (*node.routes.values(), node.on_error, node.on_unknown):
            if route.kind == "retry":
                targets.add(name)
                assert route.exhausted is not None
                route = route.exhausted
            if route.kind in {"goto", "pause"} and route.target is not None:
                targets.add(route.target)
        if targets - nodes.keys():
            raise RouterError("missing_target", f"planes.{plane_id}.nodes.{name}")
        edges[name] = targets
    reached: set[str] = set()
    pending = [start]
    while pending:
        name = pending.pop()
        if name not in reached:
            reached.add(name)
            pending.extend(edges[name])
    if reached != nodes.keys():
        raise RouterError("unreachable_node")
    return nodes


def parse_graph(raw: bytes) -> Graph:
    decoded = mapping(strict_json(raw))
    if decoded.get("format") != "millrace-router/v3":
        raise RouterError("unsupported_graph")
    obj = fields(
        decoded,
        {"format", "limits", "bindings", "planes"},
        {"defaults", "control_files", "seams"},
    )
    limits = fields(
        obj["limits"],
        {"max_invocations", "max_elapsed_seconds"},
        {"max_parallel_invocations"},
    )
    bindings: dict[str, str] = {}
    configs: dict[str, dict[str, object]] = {}
    for name, value in mapping(obj["bindings"]).items():
        identifier(name)
        binding = fields(value, {"adapter", "contract", "executable", "config"})
        identifier(binding["adapter"])
        text(binding["contract"])
        mapping(binding["config"])
        bindings[name] = text(binding["executable"])
        configs[name] = binding
    planes: dict[str, Plane] = {}
    for name, value in mapping(obj["planes"]).items():
        identifier(name)
        item = fields(value, {"start", "workspace", "nodes"}, {"defaults"})
        start = identifier(item["start"])
        workspace = relative(item["workspace"], owner_forbidden=True)
        planes[name] = Plane(
            start,
            workspace,
            _nodes(item["nodes"], start, bindings, name),
            common_settings(
                item.get("defaults", {}), location=f"planes.{name}.defaults"
            ),
        )
    parallel = int(number(limits.get("max_parallel_invocations", 1), integer=True))
    if not planes or not bindings or parallel > len(planes):
        raise RouterError("invalid_parallel_limit")
    dependencies = {
        name: {
            dep.plane_id
            for node in plane.nodes.values()
            for dep in node.requires_terminal
        }
        for name, plane in planes.items()
    }
    if any(
        target not in planes for targets in dependencies.values() for target in targets
    ):
        raise RouterError("invalid_dependency")
    remaining = dict(dependencies)
    while remaining:
        roots = {
            name
            for name, targets in remaining.items()
            if not targets & remaining.keys()
        }
        if not roots:
            raise RouterError("terminal_dependency_cycle")
        remaining = {
            name: targets for name, targets in remaining.items() if name not in roots
        }
    seams: dict[str, Seam] = {}
    consumers: set[tuple[str, str]] = set()
    for name, value in mapping(obj.get("seams", {})).items():
        identifier(name)
        item = fields(value, {"producer", "consumer"})
        producer = fields(item["producer"], {"plane", "node", "outcome"})
        consumer = fields(item["consumer"], {"plane", "node"})
        pp, pn, outcome = (
            identifier(producer[k]) for k in ("plane", "node", "outcome")
        )
        cp, cn = (identifier(consumer[k]) for k in ("plane", "node"))
        if (
            pp == cp
            or pp not in planes
            or cp not in planes
            or pn not in planes[pp].nodes
            or cn not in planes[cp].nodes
            or outcome not in planes[pp].nodes[pn].outcomes
            or (cp, cn) in consumers
        ):
            raise RouterError("invalid_seam")
        consumers.add((cp, cn))
        seams[name] = Seam(pp, pn, outcome, cp, cn)
    controls = tuple(relative(v) for v in strings(obj.get("control_files", [])))
    if len(set(controls)) != len(controls):
        raise RouterError("duplicate_control")
    return Graph(
        max_invocations=int(number(limits["max_invocations"], integer=True)),
        max_elapsed=number(limits["max_elapsed_seconds"]),
        bindings=bindings,
        planes=planes,
        controls=controls,
        defaults=common_settings(obj.get("defaults", {}), location="defaults"),
        binding_configs=configs,
        seams=seams,
        max_parallel=parallel,
    )


def _common_settings(
    graph: Graph,
    plane_id: str,
    node_id: str,
    *,
    settings_context: dict[str, object] | None = None,
) -> dict[str, object]:
    """Resolve one launch using explicit selected runtime authority only."""
    if settings_context is None:
        raise RouterError("context_required")
    try:
        runtime = common_settings(
            settings_context["runtime_defaults"], location="runtime_defaults"
        )
        policy = common_settings(
            settings_context["operator_policy"], policy=True, location="operator_policy"
        )
        version = number(settings_context["state_version"], integer=True)
    except KeyError as exc:
        raise RouterError("invalid_settings", "settings_context") from exc
    plane = graph.planes[plane_id]
    node = plane.nodes[node_id]
    authored = {
        key: value
        for key, value in (("binding", node.binding), ("timeout_seconds", node.timeout))
        if value is not None
    }
    requested: dict[str, object] = {}
    effective: dict[str, object] = {}
    provenance: dict[str, object] = {}
    warnings: list[dict[str, object]] = []
    for key in ("binding", "timeout_seconds"):
        for source, layer in (
            ("node", authored),
            ("plane", plane.defaults),
            ("workflow", graph.defaults),
            ("runtime", runtime),
        ):
            if key in layer:
                value = layer[key]
                break
        else:
            if key == "binding":
                raise RouterError(
                    "missing_effective_binding", f"nodes.{node_id}.invoke.binding"
                )
            source, value = "fallback", 1800
            warnings.append(
                {
                    "code": "timeout_defaulted",
                    "plane_id": plane_id,
                    "node_id": node_id,
                    "field": key,
                }
            )
        requested[key] = value
        constraint: dict[str, object] | None = None
        if key == "binding":
            if value not in graph.bindings:
                raise RouterError("invalid_contract", f"nodes.{node_id}.invoke.binding")
            if "binding_pin" in policy:
                if value != policy["binding_pin"]:
                    raise RouterError(
                        "binding_pin_conflict", f"nodes.{node_id}.invoke.binding"
                    )
                constraint = {"kind": "pin", "value": policy["binding_pin"]}
        elif "timeout_ceiling_seconds" in policy:
            constraint = {"kind": "ceiling", "value": policy["timeout_ceiling_seconds"]}
            value = min(
                cast(float, value), cast(float, policy["timeout_ceiling_seconds"])
            )
        effective[key] = value
        provenance[key] = {"source": source, "policy": constraint}
    return {
        "plane_id": plane_id,
        "runtime_settings_version": version,
        "requested": requested,
        "effective": effective,
        "provenance": provenance,
        "warnings": warnings,
    }


@dataclass(frozen=True)
class Revision:
    revision_id: str
    graph: Graph
    files: dict[str, bytes]
    executables: dict[str, str]
    workspaces: dict[str, dict[str, object]]

    def encoded(self) -> str:
        return json.dumps(
            {
                "files": {
                    k: base64.b64encode(v).decode() for k, v in self.files.items()
                },
                "executables": self.executables,
                "workspaces": self.workspaces,
            },
            sort_keys=True,
        )


@dataclass(frozen=True)
class Candidate:
    """A coherent folder observation, including an invalid draft diagnostic."""

    candidate_id: str
    revision: Revision | None
    files: dict[str, bytes | None]
    diagnostic: str | None

    def data(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "revision_id": self.revision.revision_id if self.revision else None,
            "diagnostic": self.diagnostic,
            "files": {
                key: base64.b64encode(raw).decode() if raw is not None else None
                for key, raw in self.files.items()
            },
        }

    def changed(self, baseline: Revision) -> list[str]:
        return sorted(
            name
            for name in self.files.keys() | baseline.files.keys()
            if self.files.get(name) != baseline.files.get(name)
        )


def revision(
    files: dict[str, bytes],
    executables: dict[str, str],
    *,
    workspaces: dict[str, dict[str, object]],
) -> Revision:
    graph = parse_graph(files["workflow.json"])
    from .planes import validate_workspaces

    validate_workspaces(graph, workspaces)
    value = Revision("", graph, files, executables, workspaces)
    return Revision(
        hashlib.sha256(value.encoded().encode()).hexdigest(),
        graph,
        files,
        executables,
        workspaces,
    )


def decode_revision(encoded: str, expected_id: str) -> Revision:
    try:
        obj = fields(strict_json(encoded), {"files", "executables", "workspaces"})
        files = {
            relative(k): base64.b64decode(
                v if isinstance(v, str) else text(v), validate=True
            )
            for k, v in mapping(obj["files"]).items()
        }
        executables = {
            identifier(k): text(v) for k, v in mapping(obj["executables"]).items()
        }
        workspaces = {
            identifier(k): mapping(v) for k, v in mapping(obj["workspaces"]).items()
        }
        result = revision(files, executables, workspaces=workspaces)
        wanted = {"workflow.json", *control_paths(result.graph)}
        if result.revision_id != expected_id or set(files) != wanted:
            raise RouterError("revision_integrity")
        if executables.keys() != result.graph.bindings.keys() or any(
            not Path(v).is_absolute() for v in executables.values()
        ):
            raise RouterError("revision_integrity")
        return result
    except (KeyError, ValueError, RouterError) as exc:
        raise RouterError("revision_integrity") from exc


def _control_bytes(path: Path, limit: int | None = None) -> bytes:
    """Nonblocking open plus descriptor type check prevents FIFO capture hangs."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise RouterError("missing_control", str(path))
            raw = source.read() if limit is None else source.read(limit + 1)
            if limit is not None and len(raw) > limit:
                raise RouterError("control_too_large")
            return raw
    except OSError as exc:
        raise RouterError("missing_control", str(path)) from exc


def capture_workspaces(
    graph: Graph, project_root: Path
) -> dict[str, dict[str, object]]:
    """Live admission only: freeze real directories and cooperative write separation."""
    from .planes import validate_workspaces

    workspaces: dict[str, dict[str, object]] = {}
    for plane_id, plane in graph.planes.items():
        workspace = within(project_root, plane.workspace, owner_forbidden=True)
        declared = project_root / plane.workspace
        if not workspace.is_dir() or (graph.max_parallel > 1 and workspace != declared):
            raise RouterError("invalid_workspace", plane_id)
        info = workspace.stat()
        workspaces[plane_id] = {
            "path": str(workspace),
            "device": info.st_dev,
            "inode": info.st_ino,
        }
        for node in plane.nodes.values():
            if not within(workspace, node.cwd, owner_forbidden=True).is_dir():
                raise RouterError("invalid_cwd", f"{plane_id}:{node.cwd}")
    validate_workspaces(graph, workspaces)
    return workspaces


def verify_live_workspaces(revision: Revision, project_root: Path) -> None:
    """Startup/dispatch refuses a changed directory identity, never rewrites history."""
    if capture_workspaces(revision.graph, project_root) != revision.workspaces:
        raise RouterError("workspace_identity_changed")


def capture(
    workflow_file: Path,
    project_root: Path,
    *,
    search_path: tuple[str, ...] | None = None,
    host_paths: dict[str, str] | None = None,
) -> Revision:
    workflow_file = resolved(workflow_file)
    project_root = resolved(project_root)
    if workflow_file.name != "workflow.json" or not project_root.is_dir():
        raise RouterError("invalid_roots")

    def read() -> tuple[dict[str, bytes], dict[str, str], dict[str, dict[str, object]]]:
        try:
            files = {"workflow.json": _control_bytes(workflow_file)}
            graph = parse_graph(files["workflow.json"])
            paths = control_paths(graph)
            for path in paths:
                source = within(workflow_file.parent, path)
                files[path] = _control_bytes(source)
            workspaces = capture_workspaces(graph, project_root)
            executables: dict[str, str] = {}
            for name, executable in graph.bindings.items():
                located = (host_paths or {}).get(executable, executable)
                found = shutil.which(
                    located,
                    path=os.pathsep.join(search_path)
                    if search_path is not None
                    else None,
                )
                if found is None:
                    raise RouterError("missing_executable", executable)
                executables[name] = str(Path(found).absolute())
            return files, executables, workspaces
        except OSError as exc:
            raise RouterError("missing_control", str(exc.filename)) from exc

    for _ in range(3):
        first, second = read(), read()
        if first == second:
            return revision(first[0], first[1], workspaces=first[2])
    raise RouterError("unstable_revision")


def validate_workflow(
    workflow_file: Path,
    project_root: Path,
    *,
    settings_context: dict[str, object] | None = None,
    adapter_resolver: AdapterResolver | None = None,
) -> bool:
    """Validate command controls without requiring generated project inputs."""
    if settings_context is None:
        raise RouterError("context_required")
    paths = strings(settings_context.get("path", []))
    if any(not Path(value).is_absolute() for value in paths):
        raise RouterError("invalid_instance_path")
    host_paths = {
        text(key): text(value)
        for key, value in mapping(settings_context.get("host_paths", {})).items()
    }
    captured = capture(
        workflow_file, project_root, search_path=paths, host_paths=host_paths
    )
    resolve_nodes(
        captured,
        settings_context=settings_context,
        adapter_resolver=adapter_resolver,
        project_root=project_root,
    )
    return True


def capture_candidate(
    workflow_file: Path,
    project_root: Path,
    *,
    search_path: tuple[str, ...],
    host_paths: dict[str, str],
    baseline: Revision | None = None,
) -> Candidate:
    """Capture valid controls or stable invalid bytes; diagnostics never activate."""
    if workflow_file.name != "workflow.json":
        raise RouterError("invalid_roots")

    def read() -> tuple[dict[str, bytes | None], dict[str, str], str | None]:
        files: dict[str, bytes | None] = {}
        executables: dict[str, str] = {}
        diagnostic: str | None = None
        try:
            files["workflow.json"] = _control_bytes(resolved(workflow_file))
            graph = parse_graph(files["workflow.json"] or b"")
            paths = control_paths(graph)
        except (OSError, RouterError) as exc:
            diagnostic = exc.code if isinstance(exc, RouterError) else "missing_control"
            paths = set(baseline.files) - {"workflow.json"} if baseline else set()
        for name in paths:
            try:
                files[name] = _control_bytes(within(workflow_file.parent, name))
            except (OSError, RouterError) as exc:
                files[name] = None
                diagnostic = diagnostic or (
                    exc.code if isinstance(exc, RouterError) else "missing_control"
                )
        if diagnostic is None:
            try:
                valid = {name: raw for name, raw in files.items() if raw is not None}
                graph = parse_graph(valid["workflow.json"])
                for name, executable in graph.bindings.items():
                    found = shutil.which(
                        host_paths.get(executable, executable),
                        path=os.pathsep.join(search_path),
                    )
                    if found is None:
                        raise RouterError("missing_executable", executable)
                    executables[name] = str(Path(found).absolute())
            except RouterError as exc:
                diagnostic = exc.code
        return files, executables, diagnostic

    for _ in range(3):
        first, second = read(), read()
        if first != second:
            continue
        files, executables, diagnostic = first
        valid = None
        if diagnostic is None:
            try:
                valid = revision(
                    {name: raw for name, raw in files.items() if raw is not None},
                    executables,
                    workspaces=dict(baseline.workspaces)
                    if baseline is not None
                    else {
                        name: {
                            "path": str(project_root / plane.workspace),
                            "device": 0,
                            "inode": 0,
                        }
                        for name, plane in parse_graph(
                            files["workflow.json"] or b""
                        ).planes.items()
                    },
                )
            except RouterError as exc:
                diagnostic = exc.code
        if valid is not None:
            candidate_id = valid.revision_id
        else:
            encoded = json.dumps(
                {
                    "files": {
                        name: base64.b64encode(raw).decode()
                        if raw is not None
                        else None
                        for name, raw in files.items()
                    },
                    "diagnostic": diagnostic,
                },
                sort_keys=True,
            )
            candidate_id = hashlib.sha256(encoded.encode()).hexdigest()
        return Candidate(candidate_id, valid, files, diagnostic)
    raise RouterError("unstable_revision")


def control_paths(graph: Graph) -> set[str]:
    return set(graph.controls)


def _namespace(settings: dict[str, object], adapter: str) -> dict[str, object]:
    return mapping(mapping(settings.get("adapter_settings", {})).get(adapter, {}))


def validate_adapters(
    revision: Revision,
    context: dict[str, object],
    adapter_resolver: AdapterResolver | None,
) -> None:
    import copy

    graph = revision.graph
    runtime = common_settings(context["runtime_defaults"])
    selected = {
        (plane_id, node_id): _common_settings(
            graph, plane_id, node_id, settings_context=context
        )
        for plane_id, plane in graph.planes.items()
        for node_id in plane.nodes
    }
    contracts: dict[str, str] = {}
    for binding in graph.binding_configs.values():
        name, contract = str(binding["adapter"]), str(binding["contract"])
        if name in contracts and contracts[name] != contract:
            raise RouterError("unsupported_adapter_contract")
        contracts[name] = contract
    for layer in (
        runtime,
        graph.defaults,
        *(plane.defaults for plane in graph.planes.values()),
    ):
        for name, raw in mapping(layer.get("adapter_settings", {})).items():
            entry = mapping(raw)
            contract = str(entry["contract"])
            if name in contracts and contracts[name] != contract:
                raise RouterError("unsupported_adapter_contract")
            contracts[name] = contract
    for name, contract in contracts.items():
        bindings = {
            key: mapping(value["config"])
            for key, value in graph.binding_configs.items()
            if value["adapter"] == name
        }
        nodes: dict[tuple[str, str], tuple[str, dict[str, object]]] = {}
        for (plane_id, node_id), common in selected.items():
            binding_id = str(mapping(common["effective"])["binding"])
            if graph.binding_configs[binding_id]["adapter"] == name:
                nodes[plane_id, node_id] = (
                    binding_id,
                    graph.planes[plane_id].nodes[node_id].config,
                )
        _adapter(name, contract, adapter_resolver).validate_configuration(
            AdapterConfiguration(
                adapter_id=name,
                adapter_contract=contract,
                bindings=copy.deepcopy(bindings),
                nodes=copy.deepcopy(nodes),
                runtime=copy.deepcopy(
                    mapping(_namespace(runtime, name).get("config", {}))
                ),
                workflow=copy.deepcopy(
                    mapping(_namespace(graph.defaults, name).get("config", {}))
                ),
                planes={
                    plane_id: copy.deepcopy(
                        mapping(_namespace(plane.defaults, name).get("config", {}))
                    )
                    for plane_id, plane in graph.planes.items()
                },
                files=dict(revision.files),
                host_paths={
                    k: str(v) for k, v in mapping(context.get("host_paths", {})).items()
                },
                environment_names=strings(context.get("env_allowlist", [])),
            )
        )


def build_launch(
    revision: Revision,
    plane_id: str,
    node_id: str,
    context: dict[str, object],
    controls: Path,
    *,
    adapter_resolver: AdapterResolver | None = None,
    project_root: Path = Path("/"),
    invocation_id: str = VALIDATION_ID,
    seam_input: SeamInput | None = None,
) -> tuple[LaunchPlan, dict[str, object]]:
    import copy

    validate_adapters(revision, context, adapter_resolver)
    common = _common_settings(
        revision.graph, plane_id, node_id, settings_context=context
    )
    common["producing_revision"] = revision.revision_id
    binding_id = str(mapping(common["effective"])["binding"])
    binding = revision.graph.binding_configs[binding_id]
    name, contract = str(binding["adapter"]), str(binding["contract"])
    plane = revision.graph.planes[plane_id]
    node = plane.nodes[node_id]
    request = InvocationRequest(
        invocation_id=invocation_id,
        plane_id=plane_id,
        node_id=node_id,
        producing_revision=revision.revision_id,
        binding_id=binding_id,
        adapter_id=name,
        adapter_contract=contract,
        executable=revision.executables[binding_id],
        binding_config=copy.deepcopy(mapping(binding["config"])),
        node_config=copy.deepcopy(node.config),
        runtime=copy.deepcopy(
            mapping(
                _namespace(mapping(context["runtime_defaults"]), name).get("config", {})
            )
        ),
        workflow=copy.deepcopy(
            mapping(_namespace(revision.graph.defaults, name).get("config", {}))
        ),
        plane=copy.deepcopy(
            mapping(_namespace(plane.defaults, name).get("config", {}))
        ),
        common_settings=copy.deepcopy(common),
        files=dict(revision.files),
        host_paths={
            k: str(v) for k, v in mapping(context.get("host_paths", {})).items()
        },
        environment_names=strings(context.get("env_allowlist", [])),
        outcomes=node.outcomes,
        result_contract="json-v1",
        project_root=Path(str(revision.workspaces[plane_id]["path"])),
        invocation_root=controls.parent,
        seam_input=seam_input,
    )
    adapter = _adapter(name, contract, adapter_resolver)
    try:
        plan = adapter.plan(request)
    except RouterError:
        raise
    except Exception as exc:
        raise RouterError("invalid_launch_plan") from exc
    projection = _admit(plan, request)
    projection["format"] = "millrace-router-launch/v1"
    projection["producing_revision"] = revision.revision_id
    projection["binding_id"] = binding_id
    projection["common_settings"] = common
    return plan, projection


def resolve_settings(
    revision: Revision,
    plane_id: str,
    node_id: str,
    *,
    settings_context: dict[str, object] | None = None,
    adapter_resolver: AdapterResolver | None = None,
    project_root: Path = Path("/"),
) -> dict[str, object]:
    if settings_context is None:
        raise RouterError("context_required")
    if not isinstance(revision, Revision):
        raise RouterError("revision_required")
    plan, projection = build_launch(
        revision,
        plane_id,
        node_id,
        settings_context,
        VALIDATION_ROOT / "controls",
        adapter_resolver=adapter_resolver,
        project_root=project_root,
    )
    return {
        "format": "millrace-router-launch-settings/v3",
        "common": projection["common_settings"],
        "adapter": _object(plan.settings),
    }


def resolve_nodes(
    revision: Revision,
    *,
    settings_context: dict[str, object] | None = None,
    adapter_resolver: AdapterResolver | None = None,
    project_root: Path = Path("/"),
) -> dict[str, dict[str, dict[str, object]]]:
    return {
        plane_id: {
            node: resolve_settings(
                revision,
                plane_id,
                node,
                settings_context=settings_context,
                adapter_resolver=adapter_resolver,
                project_root=project_root,
            )
            for node in sorted(plane.nodes)
        }
        for plane_id, plane in sorted(revision.graph.planes.items())
    }
