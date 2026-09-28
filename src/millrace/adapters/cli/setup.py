"""Headless setup owner service and narrow CLI transport, without setup effects."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from millrace.adapters.cli.output import CliSuccess, success_result
from millrace.adapters.cli.setup_inventory import (
    SelectionObservation,
    observe_selection,
)
from millrace.contracts.setup import (
    REQUIRED_CHECKS,
    Category,
    Disposition,
    ProposedContextWrite,
    SetupBlocker,
    SetupCheck,
    SetupRefusal,
    SetupRequest,
    SetupResponse,
    canonical,
    check_evidence,
    digest,
)


class SetupService:
    """One owner-resolved inspection session, never a serialized authority token.

    A UI can validate an edited/received transport against this session. The
    observation is obtained internally and is reread before accepting the wire.
    No caller supplies trusted observations, clocks, receipts or installed facts.
    Even an accepted inspection cannot authorize a runner or a setup mutation.
    """

    def __init__(self, request: SetupRequest = SetupRequest()) -> None:
        if type(request) is not SetupRequest:
            raise SetupRefusal("invalid_setup_request")
        request.__post_init__()
        self._request = request
        self._observation = observe_selection(request)
        self._response = _response(self._observation, datetime.now(UTC).isoformat())
        self._response.validate()

    def inspect(self) -> SetupResponse:
        return self._response

    def inspect_context_write(self, proposed: ProposedContextWrite) -> str:
        """Revalidate actual captures before reporting selected path policy."""
        from millrace.adapters.cli.context import workspace_paths
        from millrace.adapters.cli.setup_preflight import inspect_write

        self.validate_response(self._response.to_wire())
        context = self._observation.context
        if context is None or context.disposition == "block":
            raise SetupRefusal("context_preflight_unavailable")
        return inspect_write(proposed, context.coverage, workspace_paths(self._request))

    def validate_response(self, value: object) -> SetupResponse:
        """Closed staged wire validation plus separate live owner equality.

        Canonical bytes distinguish booleans from integers, reject non-JSON,
        additional fields, omitted fields and every unsupported future shape.
        The exact typed emitted shape is the admitted U-I01 subset of C01 v1.
        """
        try:
            matches = canonical(value) == canonical(self._response.to_wire())
        except (TypeError, ValueError, OverflowError):
            matches = False
        if not matches:
            raise SetupRefusal("setup_response_authority_mismatch")
        if observe_selection(self._request) != self._observation:
            raise SetupRefusal("setup_observation_changed")
        self._response.validate()
        return self._response


def _response(observation: SelectionObservation, observed_at: str) -> SetupResponse:
    selection = observation.selection
    identity = digest(selection.to_wire())
    checks: list[SetupCheck] = []
    blockers: list[SetupBlocker] = []

    def check(
        name: str, category: Category, disposition: Disposition, required: bool
    ) -> None:
        item = SetupCheck(
            name,
            "Core",
            identity,
            disposition,
            category,
            required,
            "",
            observed_at,
            required,
            f"Core.{name}.v1",
        )
        checks.append(replace(item, evidence_reference=check_evidence(selection, item)))

    def block(code: str, explanation: str, next_step: str) -> None:
        blockers.append(SetupBlocker(code, "Core", identity, explanation, next_step))

    for name, category in REQUIRED_CHECKS[selection.action_kind].items():
        disposition: Disposition = "block"
        if name.startswith("demo.") and observation.plan_disposition == "demo_observed":
            disposition = "pass"
        if name == "recipe.plan" and observation.plan_disposition == "observed":
            disposition = "pass"
        if name == "recipe.context" and observation.context is not None:
            disposition = observation.context.disposition
        check(name, category, disposition, True)
    if (
        selection.action_kind == "demo"
        and observation.plan_disposition != "demo_observed"
    ):
        block(
            "demo_unavailable",
            "The installed demo identity or its complete-wheel evidence "
            "is unavailable.",
            "Use an activated compatible virtual environment, then run "
            "millrace setup --interactive; for an unpublished candidate "
            "add --wheelhouse PATH.",
        )
    elif selection.action_kind != "demo":
        if observation.plan_disposition != "observed":
            block(
                observation.plan_disposition,
                "Workspace, package or plan authority is unavailable or inconsistent.",
                "Inspect the workspace and selected plan with read-only commands.",
            )
        if observation.context is None:
            block(
                "context_preflight_unavailable",
                "Selected-stage context authority is unavailable.",
                "Review selected context declarations before launch.",
            )
        else:
            for issue in observation.context.issues:
                if issue.code == "context_optional_omitted":
                    # Index refers to the source's selected coverage ordering;
                    # the full source metadata/capture is bound by selection.
                    stage_index = next(
                        i
                        for i, c in enumerate(observation.context.coverage)
                        if c.stage_id == issue.stage_id
                    )
                    source_index = next(
                        i
                        for i, s in enumerate(
                            observation.context.coverage[stage_index].sources
                        )
                        if s.source_ref == issue.source_ref
                        and s.source_kind == "workspace_relative_root"
                    )
                    check(
                        f"context.optional_omitted.{stage_index}.{source_index}",
                        "context",
                        "pass",
                        False,
                    )
                    continue
                block(
                    issue.code,
                    f"Stage {issue.stage_id}: {issue.code}."
                    + (
                        f" Source {issue.source_ref}."
                        if issue.source_ref is not None
                        else ""
                    ),
                    "Review selected context coverage; "
                    "runtime capture must recheck before use.",
                )
        block(
            "runner_readiness_unavailable",
            "Runner readiness and backend qualification are unavailable.",
            "Use the selected runner's supported diagnostics.",
        )
    for component in observation.installed:
        name = component.pin.distribution
        required = name in {"millrace-ai", "millrace-plus"}
        # Only complete retained wheel correlation satisfies artifact readiness.
        check(
            f"component.{name}",
            "component",
            "pass"
            if component.disposition == "artifact_correlated"
            else "block"
            if required
            else "deferred",
            required,
        )
        if required and component.disposition != "artifact_correlated":
            block(
                f"component_{component.disposition}",
                f"{name}: {component.disposition}. "
                "Installed byte correlation is not wheel attribution.",
                "Run millrace setup --interactive to verify "
                "exact installation evidence.",
            )
    core = next(c for c in observation.installed if c.pin.distribution == "millrace-ai")
    if observation.demo_command_disposition == "qualified":
        check("command.millrace.declaration", "command", "pass", True)
        check("command.millrace.origin", "command", "pass", True)
    else:
        check("command.millrace.declaration", "command", "block", True)
        check("command.millrace.origin", "command", "block", True)
        block(
            f"command_declaration_{core.command_disposition}",
            f"Installed millrace console declaration: {core.command_disposition}. "
            "A metadata declaration does not qualify executable readiness.",
            "Verify the Core console entrypoint using installation evidence.",
        )
        block(
            f"command_origin_{core.loaded_origin_disposition}",
            f"Loaded millrace origin: {core.loaded_origin_disposition}. "
            "Path correlation does not authenticate installed executable bytes.",
            "Use the intended Core installation and verify its artifact evidence.",
        )
    check("trust.acceptance", "trust", "block", True)
    block(
        "trust_acceptance_unavailable",
        "This inspection has no accepted execution disclosure.",
        "Run millrace setup --interactive to review and accept "
        "the scoped demo effects.",
    )
    return SetupResponse(
        selection,
        tuple(checks),
        tuple(blockers),
        None,
        context_coverage=observation.context.coverage
        if observation.context is not None
        else (),
    )


def handle_setup_command(namespace: object) -> CliSuccess:
    if getattr(namespace, "bounded", False):
        raise SetupRefusal("setup_uses_v1_inspection_contract")
    request = SetupRequest(
        action_kind=getattr(namespace, "action", "demo"),
        workspace=getattr(namespace, "workspace", None),
        db=getattr(namespace, "db", None),
        cas=getattr(namespace, "cas", None),
        plan_fingerprint=getattr(namespace, "plan_fingerprint", None),
        management_mode=getattr(namespace, "management_mode", "headless"),
        schema_version=getattr(namespace, "setup_schema_version", 1),
    )
    if getattr(namespace, "resume_receipt", None) is not None:
        from millrace.adapters.cli.setup_actions import SetupActionService

        if (
            getattr(namespace, "interactive", False)
            or getattr(namespace, "wheelhouse", None) is not None
            or getattr(namespace, "request_json", None) is not None
            or getattr(namespace, "setup_operation", "inspect") != "inspect"
        ):
            raise SetupRefusal("setup_resume_options_conflict")
        result = SetupActionService(request).resume(
            getattr(namespace, "resume_receipt")
        )
        return success_result(
            command="setup.resume",
            code="setup_action_" + result.outcome,
            message="Recovered setup result: "
            + result.outcome
            + ". Receipt: "
            + result.receipt_id
            + (
                ". Next: millrace setup --interactive"
                if result.outcome == "applied"
                else (
                    ". Next: inspect the retained receipt and affected files; "
                    "uncertain effects must not be repeated automatically."
                )
            ),
            data=result.to_wire(),
        )
    if getattr(namespace, "interactive", False):
        from millrace.adapters.cli.setup_interactive import handle_interactive_setup

        return handle_interactive_setup(namespace, request)
    if getattr(namespace, "wheelhouse", None) is not None:
        raise SetupRefusal("setup_wheelhouse_requires_interactive")
    if getattr(namespace, "setup_operation", "inspect") != "inspect":
        from millrace.adapters.cli.setup_actions import handle_setup_action

        return handle_setup_action(namespace, request)
    if getattr(namespace, "request_json", None) is not None:
        raise SetupRefusal("setup_inspection_has_no_action_payload")
    response = SetupService(request).inspect()
    return success_result(
        command="setup",
        code="setup_inspection",
        message="Setup is blocked.\n"
        + "\n".join(
            f"{b.code}: {b.explanation} {b.safe_next_action}" for b in response.blockers
        ),
        data=response.to_wire(),
    )
