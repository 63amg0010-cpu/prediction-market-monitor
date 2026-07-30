"""Stub-only adapters for Todo 11 privacy boundary tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import SecretStr
from scripts.release_privacy_contracts import (
    ArtifactTarget,
    CacheTarget,
    ContainmentMutation,
    DatabasePurgeMutation,
    DatabaseVerification,
    FrozenTarget,
    GitHubCommand,
    GitHubCommandResult,
    GitHubVerification,
    IncidentScope,
    ProviderVerification,
    RestoreMutation,
    WorkflowTarget,
)
from scripts.release_privacy_models import MatrixBProof

NOW = datetime(2026, 7, 29, 4, 30, tzinfo=UTC)
SOURCE_ID = UUID("11111111-1111-4111-8111-111111111111")
EPOCH_ID = UUID("22222222-2222-4222-8222-222222222222")
NONCE = UUID("33333333-3333-4333-8333-333333333333")
HASHES = tuple(character * 64 for character in "abcdef0123456789")


def scope() -> IncidentScope:
    return IncidentScope(
        source_id=SOURCE_ID,
        epoch_id=EPOCH_ID,
        activation_nonce=NONCE,
        violation_kind="privacy",
        predecessor_sha256=HASHES[0],
        reviewed_sha="a" * 40,
        approved_plan_sha256=HASHES[1],
    )


def frozen_targets() -> tuple[FrozenTarget, ...]:
    return (
        ArtifactTarget(artifact_id=7319),
        WorkflowTarget(run_id=8811, status="in_progress"),
        WorkflowTarget(run_id=9912, status="completed"),
        CacheTarget(key=SecretStr("scope/bad key?token=super-secret&x=1")),
    )


class StubDatabase:
    targets: tuple[FrozenTarget, ...]
    restored_calls: int
    real_production_calls: int
    verify_result: DatabaseVerification

    def __init__(self, targets: tuple[FrozenTarget, ...]) -> None:
        self.targets = targets
        self.restored_calls = 0
        self.real_production_calls = 0
        self.verify_result = DatabaseVerification(
            database_content_zero=True,
            database_search_zero=True,
            dcinside_intact=True,
            source_disabled=True,
            current_pointers_cleared=True,
            revision="20260727_0010",
            latest_state="restore_writing",
            verification_sha256=HASHES[5],
        )

    async def contain(self, scope: IncidentScope) -> ContainmentMutation:
        assert scope.source_id == SOURCE_ID
        return ContainmentMutation(
            observed_at=NOW,
            source_disabled=True,
            current_pointers_cleared=True,
            reads_blocked=True,
            state="deactivated",
            frozen_targets=self.targets,
            mutation_sha256=HASHES[2],
        )

    async def frozen_targets(
        self,
        scope: IncidentScope,
    ) -> tuple[FrozenTarget, ...]:
        assert scope.epoch_id == EPOCH_ID
        return self.targets

    async def purge(
        self,
        scope: IncidentScope,
        containment_sha256: str,
    ) -> DatabasePurgeMutation:
        assert scope.activation_nonce == NONCE
        assert len(containment_sha256) == 64
        return DatabasePurgeMutation(
            observed_at=NOW,
            affected_content_deleted=True,
            zero_title_body_url_hashes=True,
            dcinside_intact=True,
            deleted_row_count=12,
            mutation_sha256=HASHES[3],
        )

    async def verify(self, scope: IncidentScope) -> DatabaseVerification:
        assert scope.violation_kind == "privacy"
        return self.verify_result

    async def append_restored(
        self,
        scope: IncidentScope,
        purge_sha256: str,
        matrix_b_sha256: str,
    ) -> RestoreMutation:
        assert scope.activation_nonce == NONCE
        assert len(purge_sha256) == len(matrix_b_sha256) == 64
        self.restored_calls += 1
        return RestoreMutation(
            observed_at=NOW,
            prior_state="restore_writing",
            state="restored",
            mutation_sha256=HASHES[8],
        )


class StubGitHub:
    commands: list[GitHubCommand]
    real_network_calls: int

    def __init__(self) -> None:
        self.commands = []
        self.real_network_calls = 0

    async def execute(self, command: GitHubCommand) -> GitHubCommandResult:
        self.commands.append(command)
        return GitHubCommandResult(
            succeeded=True,
            status_sha256=HASHES[4],
        )

    async def verify_absent(
        self,
        targets: tuple[FrozenTarget, ...],
    ) -> GitHubVerification:
        return GitHubVerification(
            artifacts_absent=True,
            caches_absent=True,
            logs_return_404=True,
            checked_target_count=len(targets),
            verification_sha256=HASHES[6],
        )


class StubProvider:
    real_network_calls: int
    result: ProviderVerification

    def __init__(self) -> None:
        self.real_network_calls = 0
        self.result = ProviderVerification(
            zero_provider_binding=True,
            direct_api_zero=True,
            aliases_and_health_restored=True,
            repository_static_scan_clean=True,
            public_surfaces_static_scan_clean=True,
            provider_logs_clean=True,
            provider_log_search_conclusive=True,
            provider_logs_deleted_or_expired=True,
            static_scan_sha256=HASHES[7],
            provider_log_disposition_sha256=HASHES[9],
        )

    async def verify(self, scope: IncidentScope) -> ProviderVerification:
        assert scope.reviewed_sha == "a" * 40
        return self.result


def matrix_b() -> MatrixBProof:
    return MatrixBProof(
        command="matrix-b-terminal-chain",
        accepted=True,
        incident_class="privacy",
        durable_state="restore_writing",
        database_revision="20260727_0010",
        receipt_sha256=HASHES[10],
        health_sha256=HASHES[11],
    )
