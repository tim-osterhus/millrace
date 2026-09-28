"""Human prompts over the same typed setup owner used by JSON callers."""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

from millrace.adapters.cli.output import CliSuccess, success_result
from millrace.adapters.cli.setup_actions import SetupActionService
from millrace.adapters.cli.setup_install_evidence import inspect_evidence_source
from millrace.contracts.setup import (
    SetupConsentRequest,
    SetupIntent,
    SetupRefusal,
    SetupRequest,
    SetupResponse,
    digest,
)


def _confirm(service: SetupActionService, intent: SetupIntent) -> SetupResponse | None:
    response = service.disclose(intent)
    disclosure = response.trust_disclosure
    assert disclosure is not None
    print(
        "\n"
        + (
            "Verify installation evidence"
            if intent.operation == "acquire_install_evidence"
            else "Prepare the zero-key demo"
        ),
        file=sys.stderr,
    )
    print(disclosure.network_boundary, file=sys.stderr)
    for root in disclosure.roots:
        print(f"  {root.access}: {root.path}", file=sys.stderr)
    for effect in disclosure.host_mutations:
        if effect.startswith("Exact source and destination: "):
            import json

            plan = json.loads(effect.removeprefix("Exact source and destination: "))
            print("  Source: " + plan["source"], file=sys.stderr)
            print("  Evidence destination: " + plan["destination"], file=sys.stderr)
            for wheel in plan["wheels"]:
                print(
                    f"  {wheel['distribution']} {wheel['version']}: "
                    f"{wheel['filename']}",
                    file=sys.stderr,
                )
                print("    Installed at: " + wheel["installed_root"], file=sys.stderr)
                print(
                    "    Whole-wheel hash: "
                    + (
                        wheel["sha256"]
                        or "verified from the exact PyPI download after consent"
                    ),
                    file=sys.stderr,
                )
        else:
            print("  " + effect, file=sys.stderr)
    print("Allow these scoped effects? [y/N] ", file=sys.stderr, end="", flush=True)
    accepted = sys.stdin.readline().strip().lower() in {"y", "yes"}
    consent = service.accept(
        SetupConsentRequest(
            intent,
            response.selection_digest,
            disclosure.disclosure_digest,
            "human-consent:"
            + digest(
                {
                    "intent": intent.idempotency_key,
                    "selection": response.selection_digest,
                    "disclosure": disclosure.disclosure_digest,
                }
            )[7:],
            accepted,
        )
    )
    if consent is None:
        return None
    receipt = consent.trust_acceptance.receipt_id
    print("Consent receipt: " + receipt, file=sys.stderr, flush=True)
    print(
        "If interrupted, recover with: millrace setup --resume-receipt " + receipt,
        file=sys.stderr,
        flush=True,
    )
    proposed = None
    try:
        proposed = service.propose(receipt)
    except SetupRefusal as exc:
        if str(exc) != "setup_action_already_started":
            raise
    result = service.resume(receipt)
    print(
        "Result receipt: " + result.receipt_id + " (" + result.outcome + ")",
        file=sys.stderr,
        flush=True,
    )
    if result.outcome != "applied":
        raise SetupRefusal(
            "setup_action_"
            + result.outcome
            + "; inspect receipt "
            + result.receipt_id
            + "; do not repeat uncertain effects"
        )
    return proposed if proposed is not None else service.service.inspect()


def confirm_demo_trust(service: SetupActionService) -> SetupResponse | None:
    """Present and explicitly accept demo effects through the typed owner."""
    return _confirm(
        service,
        SetupIntent(
            "disclose_trust", "millrace demo", None, "human-demo:" + uuid4().hex
        ),
    )


def handle_interactive_setup(namespace: object, request: SetupRequest) -> CliSuccess:
    if getattr(namespace, "json", False) or not sys.stdin.isatty():
        raise SetupRefusal("setup_interactive_requires_text_terminal")
    if (
        request.action_kind != "demo"
        or getattr(namespace, "setup_operation", "inspect") != "inspect"
        or getattr(namespace, "request_json", None) is not None
    ):
        raise SetupRefusal("setup_interactive_demo_only")
    source_arg = getattr(namespace, "wheelhouse", None)
    source = str(Path(source_arg).absolute()) if source_arg is not None else "pypi"
    service = SetupActionService(request)
    service.check_evidence_recovery()
    observed = service.service.inspect()
    print(
        "Millrace setup — isolated synthetic demo; "
        "no model keys, OS, tmux or backend required.",
        file=sys.stderr,
    )
    for pin in observed.selection.component_pins:
        print(f"  {pin.distribution}: {pin.version}", file=sys.stderr)
    required = [
        p
        for p in observed.selection.component_pins
        if p.distribution in {"millrace-ai", "millrace-plus"}
    ]
    if len(required) != 2 or any(p.artifact_sha256 is None for p in required):
        plan = inspect_evidence_source(source)
        intent = SetupIntent(
            "acquire_install_evidence",
            source,
            None,
            "human-evidence:" + digest(plan)[7:],
        )
        if _confirm(service, intent) is None:
            return _declined()
        service = SetupActionService(request)
    response = service.service.inspect()
    remaining = [
        b for b in response.blockers if b.code != "trust_acceptance_unavailable"
    ]
    if remaining:
        return success_result(
            command="setup",
            code="setup_blocked",
            message="Setup remains blocked.\n"
            + "\n".join(b.explanation + " " + b.safe_next_action for b in remaining),
            data=response.to_wire(),
        )
    ready = confirm_demo_trust(service)
    if ready is None:
        return _declined()
    return success_result(
        command="setup",
        code="setup_ready",
        message="Setup is ready for the isolated synthetic demo.\nNext: millrace demo",
        data=ready.to_wire(),
    )


def _declined() -> CliSuccess:
    return success_result(
        command="setup",
        code="setup_consent_declined",
        message=(
            "Consent declined; no effects from this decision.\n"
            "Next: millrace setup --interactive"
        ),
        data={"accepted": False},
    )
