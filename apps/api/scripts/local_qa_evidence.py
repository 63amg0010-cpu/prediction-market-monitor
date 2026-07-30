"""Generate schema-valid, public, tests-only activation evidence for local QA."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from uuid import NAMESPACE_URL, uuid5

from scripts.activation_evidence_models import (
    ActivationEvidenceReceipt,
    PublicActivationAttestation,
    canonical_attestation_bytes,
)

if TYPE_CHECKING:
    from collections.abc import Generator, MutableMapping
    from pathlib import Path

EVIDENCE_DIRECTORY: Final = ".local-qa-activation-evidence"
ATTESTATION_NAME: Final = "activation-attestation-generation-1.json"
RECEIPT_NAME: Final = "activation-evidence-receipt.json"
PRODUCTION_CREDENTIAL_ENV_NAMES: Final = (
    "MIGRATION_DATABASE_URL",
    "PG_DUMP_DATABASE_URL",
    "PG_RESTORE_DATABASE_URL",
    "MANIFOLD_ACTIVATION_ATTESTATION_JSON",
    "MANIFOLD_API_KEY",
    "MANIFOLD_TOKEN",
    "SUPABASE_ACCESS_TOKEN",
    "VERCEL_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)


@dataclass(frozen=True, slots=True)
class LocalQaEvidence:
    """Exact ephemeral paths and public hashes bound into migration 0011."""

    attestation_path: Path
    receipt_path: Path
    attestation_sha256: str
    database_time: datetime


def materialize(attempt_dir: Path, reviewed_sha: str) -> LocalQaEvidence:
    """Write canonical model-produced evidence into one fresh attempt directory."""
    evidence_root = attempt_dir / EVIDENCE_DIRECTORY
    database_time = datetime.now(UTC).replace(microsecond=0)
    activation_nonce = uuid5(NAMESPACE_URL, f"local-qa:{reviewed_sha}:activation")
    attestation = PublicActivationAttestation.model_validate(
        {
            "schema_version": 1,
            "reviewed_sha": reviewed_sha,
            "activation_nonce": activation_nonce,
            "attestation_generation": 1,
            "source_scope_version": "phase1-reviewed-v1",
            "authorization_evidence_sha256": _digest(reviewed_sha, "authorization"),
            "free_tier_evidence_sha256": _digest(reviewed_sha, "free-tier"),
            "provenance_sha256": _digest(reviewed_sha, "provenance"),
            "predecessor_attestation_sha256": None,
            "captured_at": database_time,
            "evidence_database_time": database_time,
            "public_evidence_urls": (
                "https://github.com/63amg0010-cpu/prediction-market-monitor",
            ),
        }
    )
    attestation_bytes = canonical_attestation_bytes(attestation)
    attestation_sha = hashlib.sha256(attestation_bytes).hexdigest()
    receipt = ActivationEvidenceReceipt.model_validate(
        {
            "activation_nonce": activation_nonce,
            "attestation_generation": 1,
            "attestation_sha256": attestation_sha,
            "reservation_receipt_sha256": _digest(reviewed_sha, "reservation"),
            "dispatch_nonce": uuid5(
                NAMESPACE_URL,
                f"local-qa:{reviewed_sha}:dispatch",
            ),
            "attempt": 1,
            "run_id": 1,
            "run_attempt": 1,
            "head_sha": reviewed_sha,
            "database_time": database_time,
        }
    )
    receipt_bytes = json.dumps(
        receipt.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    attestation_path = evidence_root / ATTESTATION_NAME
    receipt_path = evidence_root / RECEIPT_NAME
    evidence_root.mkdir(exist_ok=False)
    try:
        _ = attestation_path.write_bytes(attestation_bytes)
        _ = receipt_path.write_bytes(receipt_bytes)
    except OSError:
        attestation_path.unlink(missing_ok=True)
        receipt_path.unlink(missing_ok=True)
        evidence_root.rmdir()
        raise
    return LocalQaEvidence(
        attestation_path=attestation_path,
        receipt_path=receipt_path,
        attestation_sha256=attestation_sha,
        database_time=database_time,
    )


def cleanup(evidence: LocalQaEvidence | None) -> None:
    """Remove only the two generated public evidence files and their empty dir."""
    if evidence is None:
        return
    for path in (evidence.attestation_path, evidence.receipt_path):
        path.unlink(missing_ok=True)
    evidence.attestation_path.parent.rmdir()


def sanitize_child_environment(environment: MutableMapping[str, str]) -> None:
    """Remove every Production credential and caller-owned evidence path."""
    for name in PRODUCTION_CREDENTIAL_ENV_NAMES:
        _ = environment.pop(name, None)
    _ = environment.pop("MIGRATION_ACTIVATION_ATTESTATION_PATH", None)
    _ = environment.pop("MIGRATION_ACTIVATION_EVIDENCE_RECEIPT_PATH", None)


@contextmanager
def command_nine_environment(
    environment: MutableMapping[str, str],
    attempt_dir: Path,
    reviewed_sha: str,
    *,
    enabled: bool,
) -> Generator[None]:
    """Bind generated paths only around command 9 and always remove them."""
    if not enabled:
        yield
        return
    evidence = materialize(attempt_dir, reviewed_sha)
    try:
        environment["MIGRATION_ACTIVATION_ATTESTATION_PATH"] = str(
            evidence.attestation_path
        )
        environment["MIGRATION_ACTIVATION_EVIDENCE_RECEIPT_PATH"] = str(
            evidence.receipt_path
        )
        yield
    finally:
        _ = environment.pop("MIGRATION_ACTIVATION_ATTESTATION_PATH", None)
        _ = environment.pop("MIGRATION_ACTIVATION_EVIDENCE_RECEIPT_PATH", None)
        cleanup(evidence)


def _digest(reviewed_sha: str, purpose: str) -> str:
    return hashlib.sha256(f"tests-only:{reviewed_sha}:{purpose}".encode()).hexdigest()


__all__ = (
    "PRODUCTION_CREDENTIAL_ENV_NAMES",
    "LocalQaEvidence",
    "cleanup",
    "command_nine_environment",
    "materialize",
    "sanitize_child_environment",
)
