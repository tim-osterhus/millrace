"""Execution-stage base class."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..assets.resolver import AssetResolutionError, AssetResolver, AssetSourceKind, ResolvedAsset
from ..config import EngineConfig, StageConfig
from ..contracts import ExecutionStatus, RunnerKind, StageContext, StageResult, StageType, TaskCard
from ..paths import RuntimePaths
from ..provenance import BoundExecutionParameters
from ..runner import BaseRunner
from ..status import StatusStore, validate_stage_terminal


class StageExecutionError(RuntimeError):
    """Raised when a stage cannot produce a valid terminal marker."""


class _StageSuccessStatusDescriptor:
    """Expose stage success status as a string on the class and an enum on instances."""

    def __init__(self, status: ExecutionStatus) -> None:
        self._status = status

    def __get__(self, instance: object, owner: type[object] | None = None) -> ExecutionStatus | str:
        del owner
        if instance is None:
            return self._status.value
        return self._status


class ExecutionStage:
    """Shared execution-stage behavior."""

    stage_type: StageType
    running_status: ExecutionStatus
    success_status: ExecutionStatus
    allowed_terminal_markers: frozenset[ExecutionStatus]
    synthesized_success_status: ExecutionStatus | None = None
    _PROMPT_UNSET = object()
    kind_id: str
    prompt_asset_ref: str | None = None
    terminal_statuses: tuple[str, ...] = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        stage_type = cls.__dict__.get("stage_type")
        success_status = cls.__dict__.get("success_status")
        allowed_terminal_markers = cls.__dict__.get("allowed_terminal_markers")
        if isinstance(stage_type, StageType):
            cls.kind_id = f"execution.{stage_type.value.replace('_', '-')}"
            from ..config import default_stage_configs

            prompt_file = default_stage_configs()[stage_type].prompt_file
            cls.prompt_asset_ref = prompt_file.as_posix() if prompt_file is not None else None
        if isinstance(success_status, ExecutionStatus):
            cls.success_status = _StageSuccessStatusDescriptor(success_status)
        if isinstance(success_status, ExecutionStatus) and isinstance(allowed_terminal_markers, frozenset):
            ordered_terminal_statuses = [success_status.value]
            ordered_terminal_statuses.extend(
                marker.value
                for marker in sorted(allowed_terminal_markers, key=lambda item: item.value)
                if marker is not success_status
            )
            cls.terminal_statuses = tuple(ordered_terminal_statuses)

    def __init__(
        self,
        config: EngineConfig,
        paths: RuntimePaths,
        runners: Mapping[RunnerKind, BaseRunner],
        status_store: StatusStore,
        command: Sequence[str] | None = None,
    ) -> None:
        self.config = config
        self.paths = paths
        self.runners = runners
        self.status_store = status_store
        self.command = tuple(command) if command else ()
        self.stage_config = self._resolve_stage_config()
        self.asset_resolver = AssetResolver(self.paths.root)
        self._cached_prompt_resolution: object | ResolvedAsset | None = self._PROMPT_UNSET

    def _resolve_stage_config(self) -> StageConfig:
        try:
            return self.config.stages[self.stage_type]
        except KeyError as exc:
            raise StageExecutionError(f"missing stage config for {self.stage_type.value}") from exc

    def _resolve_prompt_asset(self) -> ResolvedAsset | None:
        if self._cached_prompt_resolution is not self._PROMPT_UNSET:
            if self._cached_prompt_resolution is None:
                return None
            return self._cached_prompt_resolution

        prompt_path = self.stage_config.prompt_file
        if prompt_path is None:
            self._cached_prompt_resolution = None
            return None
        try:
            resolved = self.asset_resolver.resolve_file(prompt_path)
        except AssetResolutionError as exc:
            raise StageExecutionError(
                f"{self.stage_type.value} prompt asset is missing: {exc}"
            ) from exc
        self._cached_prompt_resolution = resolved
        return resolved

    def _prompt_asset_text(self) -> str | None:
        resolved = self._resolve_prompt_asset()
        if resolved is None:
            return None
        return resolved.read_text(encoding="utf-8").strip()

    def _prompt_for(self, task: TaskCard | None) -> str:
        prompt_lines: list[str] = []
        prompt_asset = self._prompt_asset_text()
        prompt_resolution = self._resolve_prompt_asset()
        if prompt_asset:
            prompt_lines.append(prompt_asset)
        prompt_lines.append(f"Stage: {self.stage_type.value}")
        if task is None:
            prompt_lines.append("No active task card.")
        else:
            prompt_lines.append("Active task card:")
            prompt_lines.append(task.render_markdown())
        if prompt_resolution is not None:
            prompt_lines.append(f"Prompt asset: {prompt_resolution.resolved_ref}")
        return "\n\n".join(prompt_lines).rstrip("\n") + "\n"

    def _resolve_terminal_status(self, stage_result: StageResult) -> ExecutionStatus:
        runner_result = stage_result.runner_result
        if runner_result is None:
            raise StageExecutionError(f"{self.stage_type.value} runner result is missing")

        if runner_result.detected_marker is not None:
            try:
                marker = ExecutionStatus(runner_result.detected_marker)
            except ValueError as exc:
                raise StageExecutionError(
                    f"{self.stage_type.value} emitted unknown marker {runner_result.detected_marker!r}"
                ) from exc
            validate_stage_terminal(self.stage_type, marker)
            return marker

        current_status = self.status_store.read()
        if isinstance(current_status, ExecutionStatus) and current_status in self.allowed_terminal_markers:
            validate_stage_terminal(self.stage_type, current_status)
            return current_status

        if (
            runner_result.exit_code == 0
            and current_status in {ExecutionStatus.IDLE, self.running_status}
            and self.synthesized_success_status is not None
        ):
            validate_stage_terminal(self.stage_type, self.synthesized_success_status)
            return self.synthesized_success_status

        raise StageExecutionError(
            f"{self.stage_type.value} exited {runner_result.exit_code} without a legal terminal marker"
        )

    def is_success_status(self, status: ExecutionStatus) -> bool:
        """Return whether a terminal status is this stage's successful outcome."""

        return status is self.success_status

    def run(
        self,
        task: TaskCard | None,
        run_id: str,
        *,
        allow_search_override: bool | None = None,
        allow_network_override: bool | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> StageResult:
        self.status_store.transition(self.running_status)
        prompt_resolution = self._resolve_prompt_asset()
        allow_search = self.stage_config.allow_search if allow_search_override is None else allow_search_override
        allow_network = True if allow_network_override is None else allow_network_override

        runner = self.runners[self.stage_config.runner]
        context = StageContext.model_validate(
            {
                "stage": self.stage_type,
                "runner": self.stage_config.runner,
                "model": self.stage_config.model,
                "prompt": self._prompt_for(task),
                "working_dir": self.paths.root,
                "run_id": run_id,
                "timeout_seconds": self.stage_config.timeout_seconds,
                "command": self.command,
                "prompt_path": (
                    prompt_resolution.prompt_path
                    if prompt_resolution is not None and prompt_resolution.source_kind is AssetSourceKind.WORKSPACE
                    else None
                ),
                "status_fallback_path": self.paths.status_file,
                "allow_search": allow_search,
                "allow_network": allow_network,
                "effort": self.stage_config.effort,
                "env": dict(extra_env or {}),
            }
        )
        runner_result = runner.execute(context)
        terminal_status = self._resolve_terminal_status(
            StageResult.model_validate(
                {
                    "stage": self.stage_type,
                    "status": runner_result.detected_marker or "missing",
                    "exit_code": runner_result.exit_code,
                    "runner_result": runner_result,
                }
            )
        )
        self.status_store.confirm_transition(terminal_status, previous=self.running_status)
        bound_parameters = BoundExecutionParameters(
            runner=context.runner,
            model=context.model,
            effort=context.effort,
            allow_search=context.allow_search,
            timeout_seconds=context.timeout_seconds,
        )
        return StageResult.model_validate(
            {
                "stage": self.stage_type,
                "status": terminal_status.value,
                "exit_code": runner_result.exit_code,
                "metadata": (
                    {
                        "asset_resolution": prompt_resolution.to_payload(),
                        "bound_execution_parameters": bound_parameters.model_dump(mode="json"),
                        "policy_execution_context": {
                            "allow_search": context.allow_search,
                            "allow_network": context.allow_network,
                        },
                    }
                    if prompt_resolution is not None
                    else {
                        "bound_execution_parameters": bound_parameters.model_dump(mode="json"),
                        "policy_execution_context": {
                            "allow_search": context.allow_search,
                            "allow_network": context.allow_network,
                        },
                    }
                ),
                "runner_result": runner_result,
            }
        )
