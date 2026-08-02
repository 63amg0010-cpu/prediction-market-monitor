"""Database-timed attestation and protected-stdin upload adapters."""

# pyright: reportAny=false, reportArgumentType=false, reportCallIssue=false
# ruff: noqa: TC001, TC003

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import final
from uuid import UUID

from scripts.release_evidence_attestation import (
    attest,
    attestation_secret_upload,
)
from scripts.release_evidence_contracts import AttestationArtifact
from scripts.release_gate_cli_database import with_database_clock
from scripts.release_gate_cli_io import (
    read_bytes,
    read_document,
    strings,
    write_document,
)
from scripts.release_gate_cli_subprocess import DispatchSubprocessRunner


@final
class _SecretRunner:
    def __init__(self) -> None:
        self._runner: DispatchSubprocessRunner = DispatchSubprocessRunner()

    def run(self, argv: tuple[str, ...], stdin: bytes) -> int:
        return self._runner.run(argv, stdin).returncode


def run_attest(args: argparse.Namespace) -> int:
    captures = tuple(
        read_document(path) for path in strings(args.provider_capture)
    )

    def create(clock: Callable[[], datetime]) -> AttestationArtifact:
        return attest(
            provider_captures=captures,
            authorization_live_proof=read_document(args.authorization_live_proof),
            free_tier_result=read_document(args.free_tier_result),
            measurement_receipt=read_document(args.measurement_receipt),
            attestation_generation=args.attestation_generation,
            database_time=clock(),
            source_scope_version=args.source_scope_version,
            predecessor_attestation_sha256=args.predecessor_attestation_sha256,
            public_evidence_urls=strings(args.public_evidence_url),
            expected_sha=args.expected_sha,
            expected_plan_sha256=args.expected_plan_sha256,
            activation_nonce=UUID(args.activation_nonce),
            predecessor_receipt=read_document(args.predecessor_receipt),
        )

    artifact = with_database_clock(args.database_url_env, create)
    Path(args.attestation_out).parent.mkdir(parents=True, exist_ok=True)
    _ = Path(args.attestation_out).write_bytes(artifact.canonical_attestation)
    write_document(args.json_out, artifact.receipt)
    return 0


def run_upload(args: argparse.Namespace) -> int:
    def upload(_clock: object) -> dict[str, object]:
        return attestation_secret_upload(
            _SecretRunner(),
            canonical_attestation=read_bytes(args.attestation),
            predecessor_receipt=read_document(args.predecessor_receipt),
            expected_sha=args.expected_sha,
            expected_plan_sha256=args.expected_plan_sha256,
            activation_nonce=UUID(args.activation_nonce),
        )

    receipt = with_database_clock(args.database_url_env, upload)
    write_document(args.json_out, receipt)
    return 0


HANDLERS = {
    "attest": run_attest,
    "attestation-secret-upload": run_upload,
}

__all__ = ("HANDLERS",)
