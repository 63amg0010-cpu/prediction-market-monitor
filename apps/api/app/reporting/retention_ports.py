"""Transactional persistence boundary for restrictive report retention."""

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from .retention_types import (
    ManifestItemReference,
    RetainedManifest,
    RetentionTombstone,
    SourceEntity,
    TombstoneCandidate,
)


class RetentionTransaction(Protocol):
    """Locked operations that must commit or roll back as one unit."""

    async def lock_source(self, entity_id: UUID) -> SourceEntity | None:
        """Lock and return one cleanup candidate."""
        ...

    async def lock_live_references(
        self, entity_id: UUID
    ) -> tuple[ManifestItemReference, ...]:
        """Lock every manifest item still pointing at the source row."""
        ...

    async def lock_manifest(self, manifest_id: UUID) -> RetainedManifest | None:
        """Lock one retained canonical manifest."""
        ...

    async def upsert_tombstone(
        self, candidate: TombstoneCandidate
    ) -> RetentionTombstone:
        """Create or reuse the exact deduplication identity under lock."""
        ...

    async def switch_reference(self, reference_id: UUID, tombstone_id: UUID) -> None:
        """Replace one restrictive live link with its tombstone link."""
        ...

    async def delete_source(self, entity_id: UUID) -> None:
        """Delete only when no live manifest reference remains."""
        ...

    async def lock_expired_manifests(
        self, observed_at: datetime
    ) -> tuple[RetainedManifest, ...]:
        """Lock complete report graphs whose retention boundary elapsed."""
        ...

    async def delete_report_dependencies(self, manifest_id: UUID) -> None:
        """Remove report pointer and version dependencies first."""
        ...

    async def delete_manifest_items(self, manifest_id: UUID) -> None:
        """Remove manifest item and tombstone links second."""
        ...

    async def delete_manifest(self, manifest_id: UUID) -> None:
        """Remove the canonical payload only after its items."""
        ...

    async def lock_unreferenced_tombstones(
        self, observed_at: datetime
    ) -> tuple[RetentionTombstone, ...]:
        """Lock expired tombstones with no remaining item links."""
        ...

    async def delete_tombstone(self, tombstone_id: UUID) -> None:
        """Remove one verified zero-reference tombstone last."""
        ...


class RetentionRepository(Protocol):
    """Provider of atomic, rollback-capable retention transactions."""

    def transaction(self) -> AbstractAsyncContextManager[RetentionTransaction]:
        """Open a transaction implementing restrictive deletion ordering."""
        ...
