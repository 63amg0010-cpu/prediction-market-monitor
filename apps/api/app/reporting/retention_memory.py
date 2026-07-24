"""Rollback-capable in-memory adapter for retention contract tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, final
from uuid import uuid4

import anyio
from anyio.lowlevel import checkpoint

from .retention_types import (
    ManifestItemReference,
    RetainedManifest,
    RetentionTombstone,
    SourceEntity,
    TombstoneCandidate,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from datetime import datetime
    from uuid import UUID

    from .retention_ports import RetentionTransaction


@final
class InMemoryRetentionRepository:
    """Reference adapter enforcing the restrictive ordering of the port."""

    def __init__(
        self,
        *,
        sources: tuple[SourceEntity, ...],
        manifests: tuple[RetainedManifest, ...],
        references: tuple[ManifestItemReference, ...],
    ) -> None:
        """Seed immutable source, manifest, and reference contract fixtures."""
        self._lock: anyio.Lock = anyio.Lock()
        self._sources: dict[UUID, SourceEntity] = {item.id: item for item in sources}
        self._manifests: dict[UUID, RetainedManifest] = {
            item.id: item for item in manifests
        }
        self._references: dict[UUID, ManifestItemReference] = {
            item.id: item for item in references
        }
        self._tombstones: dict[UUID, RetentionTombstone] = {}
        self._report_dependencies: set[UUID] = set(self._manifests)
        self._events: list[str] = []
        self.source_read_count: int = 0

    @property
    def tombstones(self) -> tuple[RetentionTombstone, ...]:
        """Return a stable immutable tombstone snapshot."""
        return tuple(sorted(self._tombstones.values(), key=lambda item: str(item.id)))

    @property
    def events(self) -> tuple[str, ...]:
        """Return deletion-order observables for contract verification."""
        return tuple(self._events)

    def source(self, entity_id: UUID) -> SourceEntity | None:
        """Inspect source state without counting as a production-port read."""
        return self._sources.get(entity_id)

    def reference(self, reference_id: UUID) -> ManifestItemReference | None:
        """Inspect one manifest reference after a committed transaction."""
        return self._references.get(reference_id)

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[RetentionTransaction]:
        """Commit atomically or restore every mutable collection on failure."""
        async with self._lock:
            snapshot = (
                dict(self._sources),
                dict(self._manifests),
                dict(self._references),
                dict(self._tombstones),
                set(self._report_dependencies),
                list(self._events),
            )
            completed = False
            try:
                yield self
                completed = True
            finally:
                if not completed:
                    (
                        self._sources,
                        self._manifests,
                        self._references,
                        self._tombstones,
                        self._report_dependencies,
                        self._events,
                    ) = snapshot

    async def lock_source(self, entity_id: UUID) -> SourceEntity | None:
        """Lock and return one cleanup candidate."""
        await checkpoint()
        self.source_read_count += 1
        return self._sources.get(entity_id)

    async def lock_live_references(
        self, entity_id: UUID
    ) -> tuple[ManifestItemReference, ...]:
        """Lock every reference whose restrictive live link matches."""
        await checkpoint()
        return tuple(
            sorted(
                (
                    item
                    for item in self._references.values()
                    if item.live_source_entity_id == entity_id
                ),
                key=lambda item: str(item.id),
            )
        )

    async def lock_manifest(self, manifest_id: UUID) -> RetainedManifest | None:
        """Lock and return one retained payload."""
        await checkpoint()
        return self._manifests.get(manifest_id)

    async def upsert_tombstone(
        self, candidate: TombstoneCandidate
    ) -> RetentionTombstone:
        """Deduplicate by exact identity and extend shared retention."""
        await checkpoint()
        existing = next(
            (item for item in self.tombstones if item.identity == candidate.identity),
            None,
        )
        if existing is not None:
            extended = replace(
                existing,
                retain_until=max(existing.retain_until, candidate.retain_until),
            )
            self._tombstones[existing.id] = extended
            return extended
        tombstone = RetentionTombstone(
            id=uuid4(),
            identity=candidate.identity,
            source_id=candidate.source_id,
            published_or_observed_at=candidate.published_or_observed_at,
            deleted_at=candidate.deleted_at,
            deletion_reason=candidate.deletion_reason,
            first_manifest_id=candidate.first_manifest_id,
            retain_until=candidate.retain_until,
        )
        self._tombstones[tombstone.id] = tombstone
        return tombstone

    async def switch_reference(self, reference_id: UUID, tombstone_id: UUID) -> None:
        """Replace a live link and emit a binary ordering observable."""
        await checkpoint()
        reference = self._references[reference_id]
        self._references[reference_id] = replace(
            reference,
            live_source_entity_id=None,
            tombstone_id=tombstone_id,
        )
        self._events.append("reference_switched")

    async def delete_source(self, entity_id: UUID) -> None:
        """Reject deletion while any restrictive live link remains."""
        await checkpoint()
        if any(
            item.live_source_entity_id == entity_id
            for item in self._references.values()
        ):
            message = "live_reference_prevents_source_deletion"
            raise RuntimeError(message)
        del self._sources[entity_id]
        self._events.append("source_deleted")

    async def lock_expired_manifests(
        self, observed_at: datetime
    ) -> tuple[RetainedManifest, ...]:
        """Lock report graphs at or beyond their retention boundary."""
        await checkpoint()
        return tuple(
            sorted(
                (
                    item
                    for item in self._manifests.values()
                    if item.retain_until <= observed_at
                ),
                key=lambda item: (item.retain_until, str(item.id)),
            )
        )

    async def delete_report_dependencies(self, manifest_id: UUID) -> None:
        """Delete pointer and version dependencies before manifest children."""
        await checkpoint()
        self._report_dependencies.remove(manifest_id)
        self._events.append("report_dependencies_deleted")

    async def delete_manifest_items(self, manifest_id: UUID) -> None:
        """Delete all item links after report dependencies."""
        await checkpoint()
        if manifest_id in self._report_dependencies:
            message = "report_dependency_prevents_item_deletion"
            raise RuntimeError(message)
        self._references = {
            key: item
            for key, item in self._references.items()
            if item.manifest_id != manifest_id
        }
        self._events.append("manifest_items_deleted")

    async def delete_manifest(self, manifest_id: UUID) -> None:
        """Delete a payload only after all item links are absent."""
        await checkpoint()
        if any(item.manifest_id == manifest_id for item in self._references.values()):
            message = "manifest_item_prevents_manifest_deletion"
            raise RuntimeError(message)
        del self._manifests[manifest_id]
        self._events.append("manifest_deleted")

    async def lock_unreferenced_tombstones(
        self, observed_at: datetime
    ) -> tuple[RetentionTombstone, ...]:
        """Lock elapsed tombstones absent from every manifest item."""
        await checkpoint()
        referenced = {
            item.tombstone_id
            for item in self._references.values()
            if item.tombstone_id is not None
        }
        return tuple(
            item
            for item in self.tombstones
            if item.id not in referenced and item.retain_until <= observed_at
        )

    async def delete_tombstone(self, tombstone_id: UUID) -> None:
        """Delete an unreferenced tombstone after all manifest payloads."""
        await checkpoint()
        if any(item.tombstone_id == tombstone_id for item in self._references.values()):
            message = "manifest_reference_prevents_tombstone_deletion"
            raise RuntimeError(message)
        del self._tombstones[tombstone_id]
        self._events.append("tombstone_deleted")
