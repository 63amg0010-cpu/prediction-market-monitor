"""Generate schema-valid, public, tests-only activation evidence for local QA."""

from __future__ import annotations

import base64
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
from scripts.migration_dispatch_models import NoSpendReceipt, ReviewRoot

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
MIGRATION_INPUT_ENV_NAMES: Final = (
    "MIGRATION_REVIEW_ROOT_B64",
    "MIGRATION_NO_SPEND_RECEIPT_B64",
    "MIGRATION_EXPECTED_COMMIT_SHA",
    "MIGRATION_EXPECTED_PLAN_SHA256",
    "MIGRATION_ACTIVATION_NONCE",
    "MIGRATION_DISPATCH_NONCE",
    "MIGRATION_ATTEMPT",
    "MIGRATION_CORRECTION_REVIEW_ROOT_B64",
    "MIGRATION_CORRECTION_REVIEW_ROOT_SHA256",
    "MIGRATION_CORRECTION_NO_SPEND_RECEIPT_B64",
    "MIGRATION_CORRECTION_NO_SPEND_RECEIPT_SHA256",
    "MIGRATION_CORRECTION_EXPECTED_COMMIT_SHA",
    "MIGRATION_CORRECTION_EXPECTED_PLAN_SHA256",
    "MIGRATION_CORRECTION_ACTIVATION_NONCE",
    "MIGRATION_CORRECTION_DISPATCH_NONCE",
    "MIGRATION_CORRECTION_ATTEMPT",
    "MIGRATION_REBIND_REVIEW_ROOT_B64",
    "MIGRATION_REBIND_REVIEW_ROOT_SHA256",
    "MIGRATION_REBIND_NO_SPEND_RECEIPT_B64",
    "MIGRATION_REBIND_NO_SPEND_RECEIPT_SHA256",
    "MIGRATION_REBIND_EXPECTED_COMMIT_SHA",
    "MIGRATION_REBIND_EXPECTED_PLAN_SHA256",
    "MIGRATION_REBIND_ACTIVATION_NONCE",
    "MIGRATION_REBIND_DISPATCH_NONCE",
    "MIGRATION_REBIND_ATTEMPT",
    "MIGRATION_DISPATCH_REBIND_REVIEW_ROOT_B64",
    "MIGRATION_DISPATCH_REBIND_REVIEW_ROOT_SHA256",
    "MIGRATION_DISPATCH_REBIND_NO_SPEND_RECEIPT_B64",
    "MIGRATION_DISPATCH_REBIND_NO_SPEND_RECEIPT_SHA256",
    "MIGRATION_DISPATCH_REBIND_EXPECTED_COMMIT_SHA",
    "MIGRATION_DISPATCH_REBIND_EXPECTED_PLAN_SHA256",
    "MIGRATION_DISPATCH_REBIND_ACTIVATION_NONCE",
    "MIGRATION_DISPATCH_REBIND_DISPATCH_NONCE",
    "MIGRATION_DISPATCH_REBIND_ATTEMPT",
    "GITHUB_RUN_ID",
    "RUNNER_TEMP",
)


@dataclass(frozen=True, slots=True)
class LocalQaEvidence:
    """Exact ephemeral paths and public hashes bound into migration 0011."""

    attestation_path: Path
    receipt_path: Path
    attestation_sha256: str
    database_time: datetime
    backup_path: Path
    bootstrap_environment: dict[str, str]


def materialize(attempt_dir: Path, reviewed_sha: str) -> LocalQaEvidence:
    """Write canonical model-produced evidence into one fresh attempt directory."""
    evidence_root = attempt_dir / EVIDENCE_DIRECTORY
    database_time = datetime.now(UTC).replace(microsecond=0)
    activation_nonce = uuid5(
        NAMESPACE_URL, f"local-qa:{reviewed_sha}:correction-activation"
    )
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
    backup_path = evidence_root / "pre-migration.dump.age"
    bootstrap_environment = _bootstrap_environment(
        reviewed_sha,
        activation_nonce=str(activation_nonce),
        runner_temp=str(evidence_root),
    )
    try:
        _ = attestation_path.write_bytes(attestation_bytes)
        _ = receipt_path.write_bytes(receipt_bytes)
        _ = backup_path.write_bytes(
            f"tests-only-local-qa-backup:{reviewed_sha}".encode()
        )
    except OSError:
        attestation_path.unlink(missing_ok=True)
        receipt_path.unlink(missing_ok=True)
        backup_path.unlink(missing_ok=True)
        evidence_root.rmdir()
        raise
    return LocalQaEvidence(
        attestation_path=attestation_path,
        receipt_path=receipt_path,
        attestation_sha256=attestation_sha,
        database_time=database_time,
        backup_path=backup_path,
        bootstrap_environment=bootstrap_environment,
    )


def cleanup(evidence: LocalQaEvidence | None) -> None:
    """Remove only generated local-QA evidence and its empty directory."""
    if evidence is None:
        return
    for path in (
        evidence.attestation_path,
        evidence.receipt_path,
        evidence.backup_path,
    ):
        path.unlink(missing_ok=True)
    evidence.attestation_path.parent.rmdir()


def sanitize_child_environment(environment: MutableMapping[str, str]) -> None:
    """Remove every Production credential and caller-owned evidence path."""
    for name in (*PRODUCTION_CREDENTIAL_ENV_NAMES, *MIGRATION_INPUT_ENV_NAMES):
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
        environment.update(evidence.bootstrap_environment)
        yield
    finally:
        _ = environment.pop("MIGRATION_ACTIVATION_ATTESTATION_PATH", None)
        _ = environment.pop("MIGRATION_ACTIVATION_EVIDENCE_RECEIPT_PATH", None)
        for name in evidence.bootstrap_environment:
            _ = environment.pop(name, None)
        cleanup(evidence)


def _digest(reviewed_sha: str, purpose: str) -> str:
    return hashlib.sha256(f"tests-only:{reviewed_sha}:{purpose}".encode()).hexdigest()


def _bootstrap_environment(
    reviewed_sha: str,
    *,
    activation_nonce: str,
    runner_temp: str,
) -> dict[str, str]:
    plan_sha = _digest(reviewed_sha, "approved-plan")
    protected = {
        "github_repository": _digest(reviewed_sha, "github-repository"),
        "supabase_project": _digest(reviewed_sha, "supabase-project"),
        "vercel_api_project": _digest(reviewed_sha, "vercel-api-project"),
        "vercel_web_project": _digest(reviewed_sha, "vercel-web-project"),
    }
    initial_nonce = str(
        uuid5(NAMESPACE_URL, f"local-qa:{reviewed_sha}:initial-activation")
    )
    initial_root, initial_no_spend = _release_pair(
        reviewed_sha,
        plan_sha,
        initial_nonce,
        protected,
        "initial",
    )
    correction_root, correction_no_spend = _release_pair(
        reviewed_sha,
        plan_sha,
        activation_nonce,
        protected,
        "correction",
    )
    rebind_nonce = str(
        uuid5(NAMESPACE_URL, f"local-qa:{reviewed_sha}:rebind-activation")
    )
    rebind_root, rebind_no_spend = _release_pair(
        reviewed_sha,
        plan_sha,
        rebind_nonce,
        protected,
        "rebind",
    )
    dispatch_rebind_nonce = str(
        uuid5(NAMESPACE_URL, f"local-qa:{reviewed_sha}:dispatch-rebind-activation")
    )
    dispatch_rebind_root, dispatch_rebind_no_spend = _release_pair(
        reviewed_sha,
        plan_sha,
        dispatch_rebind_nonce,
        protected,
        "dispatch-rebind",
    )
    return {
        "MIGRATION_REVIEW_ROOT_B64": base64.b64encode(initial_root).decode(),
        "MIGRATION_NO_SPEND_RECEIPT_B64": base64.b64encode(initial_no_spend).decode(),
        "MIGRATION_EXPECTED_COMMIT_SHA": reviewed_sha,
        "MIGRATION_EXPECTED_PLAN_SHA256": plan_sha,
        "MIGRATION_ACTIVATION_NONCE": initial_nonce,
        "MIGRATION_DISPATCH_NONCE": str(
            uuid5(NAMESPACE_URL, f"local-qa:{reviewed_sha}:initial-dispatch")
        ),
        "MIGRATION_ATTEMPT": "1",
        "MIGRATION_CORRECTION_REVIEW_ROOT_B64": base64.b64encode(
            correction_root
        ).decode(),
        "MIGRATION_CORRECTION_REVIEW_ROOT_SHA256": hashlib.sha256(
            correction_root
        ).hexdigest(),
        "MIGRATION_CORRECTION_NO_SPEND_RECEIPT_B64": base64.b64encode(
            correction_no_spend
        ).decode(),
        "MIGRATION_CORRECTION_NO_SPEND_RECEIPT_SHA256": hashlib.sha256(
            correction_no_spend
        ).hexdigest(),
        "MIGRATION_CORRECTION_EXPECTED_COMMIT_SHA": reviewed_sha,
        "MIGRATION_CORRECTION_EXPECTED_PLAN_SHA256": plan_sha,
        "MIGRATION_CORRECTION_ACTIVATION_NONCE": activation_nonce,
        "MIGRATION_CORRECTION_DISPATCH_NONCE": str(
            uuid5(NAMESPACE_URL, f"local-qa:{reviewed_sha}:correction-dispatch")
        ),
        "MIGRATION_CORRECTION_ATTEMPT": "1",
        "MIGRATION_REBIND_REVIEW_ROOT_B64": base64.b64encode(rebind_root).decode(),
        "MIGRATION_REBIND_REVIEW_ROOT_SHA256": hashlib.sha256(rebind_root).hexdigest(),
        "MIGRATION_REBIND_NO_SPEND_RECEIPT_B64": base64.b64encode(
            rebind_no_spend
        ).decode(),
        "MIGRATION_REBIND_NO_SPEND_RECEIPT_SHA256": hashlib.sha256(
            rebind_no_spend
        ).hexdigest(),
        "MIGRATION_REBIND_EXPECTED_COMMIT_SHA": reviewed_sha,
        "MIGRATION_REBIND_EXPECTED_PLAN_SHA256": plan_sha,
        "MIGRATION_REBIND_ACTIVATION_NONCE": rebind_nonce,
        "MIGRATION_REBIND_DISPATCH_NONCE": str(
            uuid5(NAMESPACE_URL, f"local-qa:{reviewed_sha}:rebind-dispatch")
        ),
        "MIGRATION_REBIND_ATTEMPT": "1",
        "MIGRATION_DISPATCH_REBIND_REVIEW_ROOT_B64": base64.b64encode(
            dispatch_rebind_root
        ).decode(),
        "MIGRATION_DISPATCH_REBIND_REVIEW_ROOT_SHA256": hashlib.sha256(
            dispatch_rebind_root
        ).hexdigest(),
        "MIGRATION_DISPATCH_REBIND_NO_SPEND_RECEIPT_B64": base64.b64encode(
            dispatch_rebind_no_spend
        ).decode(),
        "MIGRATION_DISPATCH_REBIND_NO_SPEND_RECEIPT_SHA256": hashlib.sha256(
            dispatch_rebind_no_spend
        ).hexdigest(),
        "MIGRATION_DISPATCH_REBIND_EXPECTED_COMMIT_SHA": reviewed_sha,
        "MIGRATION_DISPATCH_REBIND_EXPECTED_PLAN_SHA256": plan_sha,
        "MIGRATION_DISPATCH_REBIND_ACTIVATION_NONCE": dispatch_rebind_nonce,
        "MIGRATION_DISPATCH_REBIND_DISPATCH_NONCE": str(
            uuid5(NAMESPACE_URL, f"local-qa:{reviewed_sha}:dispatch-rebind-dispatch")
        ),
        "MIGRATION_DISPATCH_REBIND_ATTEMPT": "1",
        "GITHUB_RUN_ID": "1",
        "RUNNER_TEMP": runner_temp,
    }


def _release_pair(
    reviewed_sha: str,
    plan_sha: str,
    activation_nonce: str,
    protected: dict[str, str],
    purpose: str,
) -> tuple[bytes, bytes]:
    root = ReviewRoot.model_validate(
        {
            "schema_version": 1,
            "command": "deployment-prestate",
            "reviewed_sha": reviewed_sha,
            "approved_plan_sha256": plan_sha,
            "approval_round_id": _digest(reviewed_sha, f"{purpose}-round"),
            "approval_launch_sha256s": (
                _digest(reviewed_sha, f"{purpose}-launch-one"),
                _digest(reviewed_sha, f"{purpose}-launch-two"),
            ),
            "activation_nonce": activation_nonce,
            "public_provider_names": ("github", "supabase", "vercel"),
            "protected_identity_hashes": protected,
        }
    )
    root_bytes = json.dumps(
        root.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
    ).encode()
    no_spend = NoSpendReceipt.model_validate(
        {
            "schema_version": 1,
            "command": "no-spend-preflight",
            "reviewed_sha": reviewed_sha,
            "approved_plan_sha256": plan_sha,
            "activation_nonce": activation_nonce,
            "predecessor_receipt_sha256": hashlib.sha256(root_bytes).hexdigest(),
            "billing_disabled": True,
            "projection_below_70_percent": True,
        }
    )
    no_spend_bytes = json.dumps(
        no_spend.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
    ).encode()
    return root_bytes, no_spend_bytes


__all__ = (
    "PRODUCTION_CREDENTIAL_ENV_NAMES",
    "LocalQaEvidence",
    "cleanup",
    "command_nine_environment",
    "materialize",
    "sanitize_child_environment",
)
