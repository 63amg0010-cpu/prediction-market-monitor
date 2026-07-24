"""Projection of durable run facts into truthful retained source coverage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.enums import AnalysisState, AuthorizationStatus, RunStatus

from .coverage import (
    CoverageRunFacts,
    SourceCoverage,
    derive_collection_status,
    ratio_decimal,
)
from .sql_input_rows import (
    PublicationSqlRow,
    ReportInputQueryRows,
    RunSqlRow,
    SourceSqlRow,
    publication_hash,
)
from .windows import ReportWindow, seoul_report_windows

if TYPE_CHECKING:
    from datetime import datetime

    from .input_policy import ReportAssemblyPolicy
    from .inputs import ReportRecord


def source_coverage(
    policy: ReportAssemblyPolicy,
    rows: ReportInputQueryRows,
    records: tuple[ReportRecord, ...],
) -> tuple[SourceCoverage, ...]:
    """Derive status, counts, cutoffs, and event clocks for each source role."""
    windows = seoul_report_windows(rows.report_date_seoul)
    items: list[SourceCoverage] = []
    for window in (windows.primary, windows.comparison):
        slots = tuple(
            item
            for item in rows.slots
            if window.start_utc <= item.due_slot_utc < window.end_utc
        )
        for source in rows.sources:
            source_runs = tuple(
                item
                for item in rows.runs
                if item.source_id == source.source_id
                and window.start_utc <= item.due_slot_utc < window.end_utc
            )
            source_records = tuple(
                item
                for item in records
                if item.source_id == source.source_id and item.role is window.role
            )
            publications = tuple(
                item
                for item in rows.publications
                if item.source_id == source.source_id
                and window.start_utc <= item.due_slot_utc < window.end_utc
            )
            items.append(
                _coverage_item(
                    policy,
                    _CoverageFacts(
                        window=window,
                        source=source,
                        expected_runs=len(slots),
                        runs=source_runs,
                        records=source_records,
                        publications=publications,
                    ),
                )
            )
    return tuple(items)


@dataclass(frozen=True, slots=True)
class _CoverageFacts:
    window: ReportWindow
    source: SourceSqlRow
    expected_runs: int
    runs: tuple[RunSqlRow, ...]
    records: tuple[ReportRecord, ...]
    publications: tuple[PublicationSqlRow, ...]


def _coverage_item(
    policy: ReportAssemblyPolicy,
    facts: _CoverageFacts,
) -> SourceCoverage:
    source = facts.source
    expected = (source.platform, source.external_key) in policy.expected_sources
    enabled = _authorization_active(source, facts.window.end_utc)
    run_facts = _run_facts(expected, enabled, facts)
    status = derive_collection_status(run_facts)
    successful = tuple(
        item for item in facts.runs if item.status is RunStatus.SUCCEEDED
    )
    failed = tuple(
        item
        for item in facts.runs
        if item.status
        in {
            RunStatus.FAILED_RETRYABLE,
            RunStatus.FAILED_TERMINAL,
            RunStatus.STALE_ABANDONED,
        }
    )
    skipped = tuple(
        item
        for item in facts.runs
        if item.status in {RunStatus.SKIPPED_POLICY, RunStatus.SKIPPED_QUOTA}
    )
    latest_success = _latest_run(successful)
    latest_attempt = _latest_run(facts.runs)
    cutoff = max(
        facts.publications,
        key=lambda item: (item.sequence, str(item.id)),
        default=None,
    )
    valid = sum(item.analysis.state is AnalysisState.VALID for item in facts.records)
    relevant = sum(
        item.analysis.state is AnalysisState.VALID and item.analysis.relevance is True
        for item in facts.records
    )
    candidate = len(facts.records)
    return SourceCoverage(
        role=facts.window.role,
        source_id=source.source_id,
        country=source.country,
        platform=source.platform,
        community=source.community,
        expected=expected,
        enabled=enabled,
        collection_status=status,
        expected_run_count=facts.expected_runs,
        successful_run_count=len(successful),
        failed_run_count=len(failed),
        skipped_run_count=len(skipped),
        candidate_count=candidate,
        valid_analysis_count=valid,
        pending_count=candidate - valid,
        relevant_count=relevant,
        cutoff_publication_sequence=None if cutoff is None else cutoff.sequence,
        cutoff_publication_manifest_id=None if cutoff is None else cutoff.id,
        cutoff_publication_manifest_hash=(
            None if cutoff is None else publication_hash(cutoff.fingerprint())
        ),
        latest_successful_run_started_at=(
            None if latest_success is None else latest_success.started_at
        ),
        latest_successful_run_finished_at=(
            None if latest_success is None else latest_success.finished_at
        ),
        latest_publication_committed_at=(
            None if cutoff is None else cutoff.committed_at
        ),
        latest_attempt_finished_at=(
            None if latest_attempt is None else latest_attempt.finished_at
        ),
        status_observed_at=_status_observed_at(source, latest_attempt, cutoff),
        coverage_numerator=valid,
        coverage_denominator=candidate,
        coverage_decimal=ratio_decimal(valid, candidate),
    )


def _authorization_active(source: SourceSqlRow, observed_at: datetime) -> bool:
    return bool(
        source.source_enabled
        and source.authorization_status is AuthorizationStatus.APPROVED
        and source.authorization_effective_at is not None
        and source.authorization_effective_at <= observed_at
        and source.authorization_expires_at is not None
        and observed_at < source.authorization_expires_at
        and source.authorization_revoked_at is None
    )


def _run_facts(
    expected: bool,
    enabled: bool,
    facts: _CoverageFacts,
) -> CoverageRunFacts:
    counts = dict.fromkeys(RunStatus, 0)
    for run in facts.runs:
        counts[run.status] += 1
    return CoverageRunFacts(
        expected=expected,
        authorization_required=True,
        authorization_active=enabled,
        expected_runs=facts.expected_runs,
        successful_runs=counts[RunStatus.SUCCEEDED],
        skipped_policy_runs=counts[RunStatus.SKIPPED_POLICY],
        skipped_quota_runs=counts[RunStatus.SKIPPED_QUOTA],
        failed_terminal_runs=counts[RunStatus.FAILED_TERMINAL],
        failed_retryable_or_abandoned_runs=(
            counts[RunStatus.FAILED_RETRYABLE] + counts[RunStatus.STALE_ABANDONED]
        ),
    )


def _latest_run(runs: tuple[RunSqlRow, ...]) -> RunSqlRow | None:
    return max(
        runs,
        key=lambda item: (
            item.finished_at is not None,
            item.finished_at,
            item.attempt,
        ),
        default=None,
    )


def _status_observed_at(
    source: SourceSqlRow,
    run: RunSqlRow | None,
    publication: PublicationSqlRow | None,
) -> datetime | None:
    expiration = (
        source.authorization_expires_at
        if source.authorization_status is AuthorizationStatus.EXPIRED
        else None
    )
    candidates = [
        item
        for item in (
            None if run is None else run.finished_at,
            None if publication is None else publication.committed_at,
            source.authorization_revoked_at,
            expiration,
            source.authorization_effective_at,
        )
        if item is not None
    ]
    return max(candidates, default=None)
