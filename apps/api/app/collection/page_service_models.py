"""Typed inputs and locked rows for transactional page commits."""

from dataclasses import dataclass
from uuid import UUID

from app.db.run_models import CollectionRun, SourceCheckpoint

from .analysis_input_store import AnalysisQueueVersions
from .page_commit import PageCommitContext, PageCommitRequest


@dataclass(frozen=True, slots=True)
class PageCommitOperation:
    """Path-bound run identity paired with its parsed page body."""

    run_id: UUID
    request: PageCommitRequest


@dataclass(frozen=True, slots=True)
class PageCommitServiceConfig:
    """Reviewed collection caps and immutable analysis versions."""

    reviewed_page_cap: int
    reviewed_post_cap: int
    analysis_versions: AnalysisQueueVersions


@dataclass(frozen=True, slots=True)
class LockedPageContext:
    """Domain snapshot paired with the mutable rows locked for its CAS."""

    domain: PageCommitContext
    run_row: CollectionRun
    checkpoint_row: SourceCheckpoint
