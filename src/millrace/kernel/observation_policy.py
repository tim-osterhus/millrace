"""Kernel import surface for durable runner observation and artifact authentication.

The pure implementation lives in ``millrace.contracts.observation_policy`` so
contracts-layer closure history authenticates captured artifacts with the same
implementation.
"""

from __future__ import annotations

from millrace.contracts.observation_policy import (
    AuthenticatedArtifactProvenance,
    AuthenticatedRunnerObservation,
    ObservationPolicyDiagnostic,
    authenticate_artifact_provenance,
    authenticate_runner_observation,
)

__all__ = (
    "AuthenticatedArtifactProvenance",
    "AuthenticatedRunnerObservation",
    "ObservationPolicyDiagnostic",
    "authenticate_artifact_provenance",
    "authenticate_runner_observation",
)
