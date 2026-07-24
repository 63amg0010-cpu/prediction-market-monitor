"""Map canonical retained report values into durable database rows."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.db.manifest_models import (
    ReportInputManifest as StoredManifest,
)
from app.db.manifest_models import (
    ReportInputManifestItem,
)
from app.db.report_models import DailyReportVersion
from app.db.tombstone_models import ReportInputManifestItemMatch
from app.domain.enums import ManifestItemKind, ReportRole
from app.services.configuration.canonical import canonical_bytes

from .manifest import ManifestEnvelope
from .repository_types import (
    AppendReportRequest,
    StoredReportVersion,
    VersionAppendState,
)
from .reproduction import RetainedReport

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from .coverage import SourceCoverage
    from .manifest_schema import ReportInputManifest
    from .report_schema import DailyReportPayload


def retained(manifest: StoredManifest, version: DailyReportVersion) -> RetainedReport:
    """Rehydrate only the two retained projection rows permitted for replay."""
    return RetainedReport(
        manifest=ManifestEnvelope(
            codec=manifest.codec,
            compressed_payload=manifest.compressed_payload,
            uncompressed_byte_length=manifest.uncompressed_byte_length,
            manifest_payload_sha256=manifest.manifest_payload_sha256,
            input_set_hash=manifest.input_set_hash,
        ),
        report_payload=version.report_payload,
        report_payload_sha256=version.report_payload_sha256,
    )


def stored(
    version: DailyReportVersion,
    retained_report: RetainedReport,
) -> StoredReportVersion:
    """Project an immutable database version into the repository contract."""
    return StoredReportVersion(
        report_id=version.report_id,
        version_id=version.id,
        manifest_id=version.manifest_id,
        report_date_seoul=version.report_date_seoul,
        revision=version.revision,
        supersedes_version_id=version.supersedes_version_id,
        report_schema_version=version.report_schema_version,
        input_set_hash=version.input_set_hash,
        created_at=version.created_at,
        retain_until=version.retain_until,
        retained=retained_report,
    )


def version_row(
    request: AppendReportRequest,
    payload: DailyReportPayload,
    state: VersionAppendState,
) -> DailyReportVersion:
    """Materialize every displayed scalar beside the canonical report bytes."""
    primary, comparison = payload.windows
    return DailyReportVersion(
        id=request.version_id,
        report_id=state.report_id,
        report_date_seoul=request.report_date_seoul,
        revision=state.revision,
        supersedes_version_id=None if state.latest is None else state.latest.id,
        input_set_hash=request.input_set_hash,
        report_schema_version=request.report_schema_version,
        manifest_id=request.manifest_id,
        primary_window_start_utc=primary.start_utc,
        primary_window_end_utc=primary.end_utc,
        comparison_window_start_utc=comparison.start_utc,
        comparison_window_end_utc=comparison.end_utc,
        formula_version=payload.formula_version,
        formula_hash=payload.formula_hash,
        metric_version=payload.metric_version,
        metric_hash=payload.metric_hash,
        category_version=payload.category_version,
        category_hash=payload.category_hash,
        candidate_count=payload.candidate_count,
        valid_analysis_count=payload.valid_analysis_count,
        pending_count=payload.pending_count,
        relevant_count=payload.relevant_count,
        positive_count=payload.positive_count,
        neutral_count=payload.neutral_count,
        negative_count=payload.negative_count,
        unknown_sentiment_count=payload.unknown_sentiment_count,
        analysis_coverage_numerator=payload.analysis_coverage_numerator,
        analysis_coverage_denominator=payload.analysis_coverage_denominator,
        analysis_coverage_decimal=payload.analysis_coverage_decimal,
        comments_sum=payload.comments_sum,
        comments_known_count=payload.comments_known_count,
        comments_unknown_count=payload.comments_unknown_count,
        score_sum=payload.score_sum,
        score_known_count=payload.score_known_count,
        score_unknown_count=payload.score_unknown_count,
        highlights=[item.model_dump(mode="json") for item in payload.highlights],
        rising_keywords=[
            item.model_dump(mode="json") for item in payload.rising_keywords
        ],
        source_coverage=[
            item.model_dump(mode="json") for item in payload.source_coverage
        ],
        status=payload.status,
        report_payload=request.retained.report_payload,
        report_payload_byte_length=len(request.retained.report_payload),
        report_payload_sha256=request.retained.report_payload_sha256,
        created_at=request.created_at,
        retain_until=request.retain_until,
    )


def manifest_row(
    request: AppendReportRequest,
    payload: ReportInputManifest,
    revision: int,
) -> StoredManifest:
    """Materialize the canonical value-complete manifest envelope and metadata."""
    windows = {item.role: item for item in payload.windows}
    definitions = payload.definitions
    envelope = request.retained.manifest
    return StoredManifest(
        id=request.manifest_id,
        report_version_id=request.version_id,
        report_date_seoul=request.report_date_seoul,
        report_revision=revision,
        schema_version=payload.schema_name,
        primary_window_date_seoul=windows[ReportRole.PRIMARY].date_seoul,
        comparison_window_date_seoul=windows[ReportRole.COMPARISON].date_seoul,
        primary_window_start_utc=windows[ReportRole.PRIMARY].start_utc,
        primary_window_end_utc=windows[ReportRole.PRIMARY].end_utc,
        comparison_window_start_utc=windows[ReportRole.COMPARISON].start_utc,
        comparison_window_end_utc=windows[ReportRole.COMPARISON].end_utc,
        source_scope_version=payload.source_scope_version,
        formula_version=definitions.formula_version,
        formula_hash=definitions.formula_hash,
        formula_constants=definitions.constants.model_dump(mode="json"),
        metric_version=definitions.metric_version,
        metric_hash=definitions.metric_hash,
        category_version=definitions.category_version,
        category_hash=definitions.category_hash,
        governing_version_tuples={
            "rule_sets": [
                item.model_dump(mode="json") for item in definitions.rule_sets
            ],
            "analysis_versions": [
                item.model_dump(mode="json") for item in definitions.analysis_versions
            ],
        },
        codec=envelope.codec,
        compressed_payload=envelope.compressed_payload,
        uncompressed_byte_length=envelope.uncompressed_byte_length,
        manifest_payload_sha256=envelope.manifest_payload_sha256,
        input_set_hash=envelope.input_set_hash,
        created_at=request.created_at,
        retain_until=request.retain_until,
    )


def add_manifest_items(
    session: AsyncSession,
    payload: ReportInputManifest,
    manifest_id: UUID,
) -> None:
    """Persist live restrictive links for every retained record and coverage slice."""
    rows: list[ReportInputManifestItem | ReportInputManifestItemMatch] = []
    for record in payload.records:
        item_id = uuid4()
        rows.append(
            ReportInputManifestItem(
                id=item_id,
                manifest_id=manifest_id,
                item_kind=ManifestItemKind.RECORD,
                ordinal=record.ordinal,
                role=record.role,
                source_id=record.source_id,
                source_entity_id=record.post_version_id,
                source_entity_hash=record.post_content_hash,
                live_post_version_id=record.post_version_id,
                post_version_tombstone_id=None,
                live_analysis_id=record.analysis.analysis_id,
                live_engagement_observation_id=record.engagement.observation_id,
                live_source_publication_manifest_id=(
                    record.source_publication_manifest_id
                ),
                provenance_values=record.model_dump(mode="json"),
                value_slice_sha256=sha256(canonical_bytes(record)).hexdigest(),
            )
        )
        rows.extend(
            ReportInputManifestItemMatch(
                id=uuid4(),
                manifest_item_id=item_id,
                post_match_id=match.match_id,
            )
            for match in record.rule_matches
        )
    for role in (ReportRole.PRIMARY, ReportRole.COMPARISON):
        coverage = tuple(item for item in payload.source_coverage if item.role is role)
        rows.extend(
            _coverage_item(manifest_id, item, ordinal)
            for ordinal, item in enumerate(coverage)
        )
    session.add_all(rows)


def _coverage_item(
    manifest_id: UUID,
    coverage: SourceCoverage,
    ordinal: int,
) -> ReportInputManifestItem:
    source_id = coverage.source_id
    manifest_source_id = coverage.cutoff_publication_manifest_id
    return ReportInputManifestItem(
        id=uuid4(),
        manifest_id=manifest_id,
        item_kind=ManifestItemKind.SOURCE_COVERAGE,
        ordinal=ordinal,
        role=coverage.role,
        source_id=source_id,
        source_entity_id=manifest_source_id,
        source_entity_hash=coverage.cutoff_publication_manifest_hash,
        live_post_version_id=None,
        post_version_tombstone_id=None,
        live_analysis_id=None,
        live_engagement_observation_id=None,
        live_source_publication_manifest_id=manifest_source_id,
        provenance_values=coverage.model_dump(mode="json"),
        value_slice_sha256=sha256(canonical_bytes(coverage)).hexdigest(),
    )
