"""Typed privacy containment, purge, and terminal verification handlers."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from scripts.release_privacy_github import frozen_graph_sha256, purge_github
from scripts.release_privacy_models import (
    ContainmentReceipt,
    MatrixBProof,
    PrivacyVerifyReceipt,
    PurgeReceipt,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from scripts.release_privacy_contracts import (
        DatabaseVerification,
        GitHubVerification,
        IncidentScope,
        PrivacyDatabase,
        PrivacyGitHub,
        PrivacyProvider,
        ProviderVerification,
    )


class PrivacyChainError(ValueError):
    """Reject a foreign or ordinary-rollback incident chain."""

    def __init__(self) -> None:
        """Create a redacted chain-integrity failure."""
        super().__init__("privacy incident chain mismatch")


def _canonical(value: JsonValue) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def model_sha256(model: IncidentScope | ContainmentReceipt | PurgeReceipt) -> str:
    """Hash a closed model without adding a self-referential receipt field."""
    payload = model.model_dump(mode="json")
    return hashlib.sha256(_canonical(payload)).hexdigest()


def scope_sha256(scope: IncidentScope) -> str:
    """Hash protected incident identifiers for public receipt correlation."""
    protected: dict[str, JsonValue] = {
        "activation_nonce": str(scope.activation_nonce),
        "approved_plan_sha256": scope.approved_plan_sha256,
        "epoch_id": str(scope.epoch_id),
        "reviewed_sha": scope.reviewed_sha,
        "source_id": str(scope.source_id),
        "violation_kind": scope.violation_kind,
    }
    return hashlib.sha256(_canonical(protected)).hexdigest()


async def privacy_contain(
    scope: IncidentScope,
    database: PrivacyDatabase,
) -> ContainmentReceipt:
    """Atomically disable, unlink, block reads, and freeze the affected graph."""
    mutation = await database.contain(scope)
    return ContainmentReceipt(
        scope_sha256=scope_sha256(scope),
        predecessor_sha256=scope.predecessor_sha256,
        mutation_sha256=mutation.mutation_sha256,
        frozen_graph_sha256=frozen_graph_sha256(mutation.frozen_targets),
        frozen_target_count=len(mutation.frozen_targets),
    )


async def privacy_purge(
    scope: IncidentScope,
    containment: ContainmentReceipt,
    database: PrivacyDatabase,
    github: PrivacyGitHub,
) -> PurgeReceipt:
    """Delete exact activation/epoch content and every frozen GitHub surface."""
    expected_scope = scope_sha256(scope)
    if (
        containment.scope_sha256 != expected_scope
        or containment.predecessor_sha256 != scope.predecessor_sha256
    ):
        raise PrivacyChainError
    containment_sha = model_sha256(containment)
    targets = await database.frozen_targets(scope)
    graph_sha = frozen_graph_sha256(targets)
    if (
        graph_sha != containment.frozen_graph_sha256
        or len(targets) != containment.frozen_target_count
    ):
        raise PrivacyChainError
    mutation = await database.purge(scope, containment_sha)
    disposition_hashes = await purge_github(github, targets)
    disposition_sha = hashlib.sha256(
        _canonical(list(disposition_hashes))
    ).hexdigest()
    return PurgeReceipt(
        scope_sha256=expected_scope,
        predecessor_sha256=containment_sha,
        containment_sha256=containment_sha,
        mutation_sha256=mutation.mutation_sha256,
        frozen_graph_sha256=graph_sha,
        deleted_row_count=mutation.deleted_row_count,
        deleted_github_object_count=len(targets),
        github_disposition_sha256=disposition_sha,
    )


def _hold_reasons(
    database: DatabaseVerification,
    github: GitHubVerification,
    provider: ProviderVerification,
    expected_target_count: int,
) -> tuple[str, ...]:
    checks = {
        "db_content_retained": database.database_content_zero,
        "db_search_retained": database.database_search_zero,
        "api_content_retained": provider.direct_api_zero,
        "dcinside_drift": database.dcinside_intact,
        "source_not_inert": (
            database.source_disabled
            and database.current_pointers_cleared
            and database.revision == "20260727_0010"
            and database.latest_state == "restore_writing"
        ),
        "github_artifact_retained": github.artifacts_absent,
        "github_cache_retained": github.caches_absent,
        "github_logs_not_404": github.logs_return_404,
        "github_target_set_unverified": (
            github.checked_target_count == expected_target_count
        ),
        "provider_binding_present": provider.zero_provider_binding,
        "alias_or_health_drift": provider.aliases_and_health_restored,
        "repository_static_scan_failed": provider.repository_static_scan_clean,
        "public_surface_scan_failed": provider.public_surfaces_static_scan_clean,
        "provider_logs_not_clean": provider.provider_logs_clean,
        "provider_log_search_inconclusive": (
            provider.provider_log_search_conclusive
        ),
        "provider_log_retention_unverified": (
            provider.provider_logs_deleted_or_expired
        ),
    }
    return tuple(code for code, passed in checks.items() if not passed)


async def privacy_verify(  # noqa: PLR0913
    scope: IncidentScope,
    containment: ContainmentReceipt,
    purge: PurgeReceipt,
    matrix_b: MatrixBProof,
    database: PrivacyDatabase,
    github: PrivacyGitHub,
    provider: PrivacyProvider,
) -> PrivacyVerifyReceipt:
    """Request terminal restored only after every privacy surface is proven clean."""
    scope_hash = scope_sha256(scope)
    containment_sha = model_sha256(containment)
    purge_sha = model_sha256(purge)
    if (
        containment.scope_sha256 != scope_hash
        or containment.predecessor_sha256 != scope.predecessor_sha256
    ):
        raise PrivacyChainError
    if purge.scope_sha256 != scope_hash:
        raise PrivacyChainError
    if (
        purge.containment_sha256 != containment_sha
        or purge.predecessor_sha256 != containment_sha
    ):
        raise PrivacyChainError
    if matrix_b.incident_class != scope.violation_kind:
        raise PrivacyChainError
    targets = await database.frozen_targets(scope)
    if frozen_graph_sha256(targets) != purge.frozen_graph_sha256:
        raise PrivacyChainError
    db_result = await database.verify(scope)
    github_result = await github.verify_absent(targets)
    provider_result = await provider.verify(scope)
    reasons = _hold_reasons(
        db_result,
        github_result,
        provider_result,
        len(targets),
    )
    common = {
        "scope_sha256": scope_hash,
        "predecessor_sha256": matrix_b.receipt_sha256,
        "containment_sha256": containment_sha,
        "purge_sha256": purge_sha,
        "matrix_b_sha256": matrix_b.receipt_sha256,
        "database_verification_sha256": db_result.verification_sha256,
        "github_verification_sha256": github_result.verification_sha256,
        "static_scan_sha256": provider_result.static_scan_sha256,
        "provider_log_disposition_sha256": (
            provider_result.provider_log_disposition_sha256
        ),
    }
    if reasons:
        return PrivacyVerifyReceipt(
            scope_sha256=common["scope_sha256"],
            predecessor_sha256=common["predecessor_sha256"],
            containment_sha256=common["containment_sha256"],
            purge_sha256=common["purge_sha256"],
            matrix_b_sha256=common["matrix_b_sha256"],
            database_verification_sha256=common[
                "database_verification_sha256"
            ],
            github_verification_sha256=common["github_verification_sha256"],
            static_scan_sha256=common["static_scan_sha256"],
            provider_log_disposition_sha256=common[
                "provider_log_disposition_sha256"
            ],
            accepted=False,
            status="PRIVACY_HOLD",
            durable_state="restore_writing",
            hold_reasons=reasons,
        )
    restored = await database.append_restored(
        scope,
        purge_sha,
        matrix_b.receipt_sha256,
    )
    return PrivacyVerifyReceipt(
        scope_sha256=common["scope_sha256"],
        predecessor_sha256=common["predecessor_sha256"],
        containment_sha256=common["containment_sha256"],
        purge_sha256=common["purge_sha256"],
        matrix_b_sha256=common["matrix_b_sha256"],
        database_verification_sha256=common[
            "database_verification_sha256"
        ],
        github_verification_sha256=common["github_verification_sha256"],
        static_scan_sha256=common["static_scan_sha256"],
        provider_log_disposition_sha256=common[
            "provider_log_disposition_sha256"
        ],
        accepted=True,
        status="RESTORED",
        durable_state=restored.state,
        restore_mutation_sha256=restored.mutation_sha256,
    )
