import gzip
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from app.domain.enums import (
    ReportRole,
    ReportStatus,
    Sentiment,
)
from app.reporting.coverage import CollectionStatus
from app.reporting.formula import project_report
from app.reporting.manifest import build_manifest, read_manifest
from app.reporting.manifest_schema import ReportInputManifest
from app.reporting.reconciliation import (
    correction_targets,
    reconcile_report,
)
from app.reporting.repository import InMemoryReportRepository
from app.reporting.reproduction import (
    ReproducedReport,
    RetainedReport,
    reproduce_report,
)
from app.reporting.retention import (
    RetentionIntegrityError,
    cleanup_source,
    expire_retained_reports,
)
from app.reporting.retention_memory import InMemoryRetentionRepository
from app.reporting.retention_types import CleanupRequest
from pydantic import ValidationError
from tests.unit.reporting.factories import (
    digest,
    manifest_payload,
    selected_engagement,
)
from tests.unit.reporting.integration_factories import (
    category_tie_report,
    late_correction_payloads,
    reconcile_request,
    relevant_record,
)
from tests.unit.reporting.test_retention import NOW, retained_fixture


def test_manifest_snapshots_all_formula_values_and_nulls() -> None:
    # Given: a relevant record with independently unknown engagement values.
    source = relevant_record(1, ReportRole.PRIMARY).model_copy(
        update={"engagement": selected_engagement(1, None, None)}
    )
    # When: the value-complete canonical manifest is serialized.
    canonical = build_manifest(manifest_payload((source,))).canonical_bytes
    missing_scalar = canonical.replace(b'"comments_count":null,', b"", 1)
    # Then: explicit nulls are retained and omitting one required value is invalid.
    assert b'"comments_count":null' in canonical
    assert b'"upvote_or_score":null' in canonical
    assert b'"sentiment":null' in canonical
    with pytest.raises(ValidationError):
        _ = ReportInputManifest.model_validate_json(missing_scalar)


def test_primary_and_comparison_change_identity() -> None:
    # Given: one P record and one Q record in a canonical baseline.
    primary = relevant_record(1, ReportRole.PRIMARY, Sentiment.POSITIVE)
    comparison = relevant_record(2, ReportRole.COMPARISON)
    base = build_manifest(manifest_payload((primary, comparison)))
    # When: a primary value and a comparison value change independently.
    changed_p = primary.model_copy(
        update={"engagement": selected_engagement(1, 0, None)}
    )
    changed_q = comparison.model_copy(
        update={"source_publication_manifest_hash": digest(9000)}
    )
    identities = {
        base.envelope.input_set_hash,
        build_manifest(
            manifest_payload((changed_p, comparison))
        ).envelope.input_set_hash,
        build_manifest(manifest_payload((primary, changed_q))).envelope.input_set_hash,
    }
    # Then: either half of P/Q changes the correction identity.
    assert len(identities) == 3


def test_categories_rules_topics_and_ties() -> None:
    # Given: three tied categories and a rule/topic duplicate on the first record.
    # When: effective categories and rising phrases are projected.
    report = category_tie_report()
    # Then: categories dedupe, ties sort by name, and one phrase counts per record.
    assert tuple(item.category for item in report.highlights) == (
        "alpha",
        "beta",
        "gamma",
    )
    assert report.rising_keywords[0].phrase == "rise"
    assert report.rising_keywords[0].primary_count == 3


def test_engagement_nulls_and_empty_windows() -> None:
    # Given: an empty manifest and a relevant record with observed zero score.
    empty = project_report(manifest_payload(())).payload
    source = relevant_record(1, ReportRole.PRIMARY).model_copy(
        update={"engagement": selected_engagement(1, None, 0)}
    )
    # When: the record-bearing report is projected.
    observed = project_report(manifest_payload((source,))).payload
    # Then: empty is truthful and unknown comments never become numeric zero.
    assert empty.status is ReportStatus.COMPLETE
    assert empty.analysis_coverage_decimal is None
    assert empty.highlights == ()
    assert observed.comments_sum is None
    assert observed.score_sum == 0


def test_source_coverage_status_and_timestamps() -> None:
    # Given: an expected primary source whose collection status is missing.
    payload = manifest_payload(())
    observed_at = datetime(2026, 7, 22, 2, 3, 4, 5, tzinfo=UTC)
    status = CollectionStatus.MISSING
    primary = payload.source_coverage[0].model_copy(
        update={
            "collection_status": status,
            "successful_run_count": 0,
            "cutoff_publication_sequence": None,
            "cutoff_publication_manifest_id": None,
            "cutoff_publication_manifest_hash": None,
            "latest_successful_run_started_at": None,
            "latest_successful_run_finished_at": None,
            "latest_publication_committed_at": None,
            "latest_attempt_finished_at": None,
            "status_observed_at": observed_at,
        }
    )
    changed = payload.model_copy(
        update={"source_coverage": (primary, payload.source_coverage[1])}
    )
    # When: retained source facts are copied into the report projection.
    report = project_report(changed).payload
    # Then: status and six-digit UTC event time reproduce exactly and force partial.
    assert report.status is ReportStatus.PARTIAL
    assert report.source_coverage[0] == primary
    assert b"2026-07-22T02:03:04.000005Z" in project_report(changed).canonical_bytes


def test_jcs_hash_is_order_and_compressor_stable() -> None:
    # Given: the same P/Q facts in opposite input order.
    primary = relevant_record(1, ReportRole.PRIMARY, Sentiment.POSITIVE)
    comparison = relevant_record(2, ReportRole.COMPARISON, Sentiment.NEUTRAL)

    # When: canonical payloads use deterministic and variable gzip headers.
    first = build_manifest(manifest_payload((comparison, primary)))
    second = build_manifest(manifest_payload((primary, comparison)))
    varied = replace(
        first.envelope,
        compressed_payload=gzip.compress(first.canonical_bytes, mtime=123),
    )

    # Then: golden bytes hash and input identity ignore order and compression metadata.
    assert first.envelope.manifest_payload_sha256 == (
        "02d55f26190cb40c7a5b6a3575ab7c1c785949b75855828d6724b9b34c4639b5"
    )
    assert first.envelope.input_set_hash == second.envelope.input_set_hash
    assert read_manifest(varied) == first.payload


@pytest.mark.asyncio
async def test_post_purge_reproduction_forbids_source_queries() -> None:
    # Given: a verified retained report and one eligible referenced source row.
    manifest, reference, source = retained_fixture()
    repository = InMemoryRetentionRepository(
        sources=(source,), manifests=(manifest,), references=(reference,)
    )
    payload = read_manifest(manifest.envelope)
    report = project_report(payload)
    retained = RetainedReport(
        manifest.envelope,
        report.canonical_bytes,
        report.payload_sha256,
    )

    # When: raw cleanup completes and reproduction reads retained bytes only.
    _ = await cleanup_source(repository, CleanupRequest(source.id, NOW))
    reads_after_purge = repository.source_read_count
    reproduced = reproduce_report(retained)

    # Then: the source is physically absent, bytes match, and no forbidden read occurs.
    assert repository.source(source.id) is None
    assert isinstance(reproduced, ReproducedReport)
    assert reproduced.report_payload == report.canonical_bytes
    assert repository.source_read_count == reads_after_purge


@pytest.mark.asyncio
async def test_cleanup_fails_closed_on_missing_value_or_hash() -> None:
    # Given: an eligible source whose retained value-slice hash is corrupt.
    manifest, reference, source = retained_fixture()
    corrupt = replace(reference, value_slice_sha256=digest(9999))
    repository = InMemoryRetentionRepository(
        sources=(source,), manifests=(manifest,), references=(corrupt,)
    )

    # When: cleanup verifies the value-bearing payload transactionally.
    with pytest.raises(RetentionIntegrityError, match="value_slice_hash_mismatch"):
        _ = await cleanup_source(repository, CleanupRequest(source.id, NOW))

    # Then: rollback preserves both the raw row and its restrictive live reference.
    assert repository.source(source.id) == source
    assert repository.reference(reference.id) == corrupt
    assert repository.tombstones == ()


@pytest.mark.asyncio
async def test_shared_tombstone_lifecycle() -> None:
    # Given: two report manifests share one immutable source value slice.
    first, first_ref, source = retained_fixture(retain_until=NOW + timedelta(days=1))
    second, second_ref, _ = retained_fixture(
        manifest_id=UUID(int=8000),
        reference_id=UUID(int=8001),
        retain_until=NOW + timedelta(days=2),
    )
    references = (first_ref, second_ref)
    repository = InMemoryRetentionRepository(
        sources=(source,), manifests=(first, second), references=references
    )
    _ = await cleanup_source(repository, CleanupRequest(source.id, NOW))

    # When: the first and final report graphs expire on successive days.
    initial = await expire_retained_reports(repository, NOW + timedelta(days=1))
    final = await expire_retained_reports(repository, NOW + timedelta(days=2))

    # Then: the shared tombstone survives first expiry and leaves after the last link.
    assert initial.deleted_tombstone_ids == ()
    assert final.deleted_tombstone_ids
    assert repository.tombstones == ()


@pytest.mark.asyncio
async def test_late_pq_corrections_are_deterministic() -> None:
    # Given: a baseline P/Q report and four independent formula-effective changes.
    stages = late_correction_payloads()

    # When: each changed snapshot and a final retry reconcile under one date lock.
    repository = InMemoryReportRepository()
    outcomes = [
        await reconcile_report(repository, reconcile_request(payload, seed))
        for seed, payload in enumerate(stages, start=1)
    ]
    duplicate = await reconcile_report(repository, reconcile_request(stages[-1], 99))
    targets = correction_targets(date(2026, 7, 21), date(2026, 7, 22))

    # Then: each identity appends once and late Q affects D and its immediate P report.
    assert tuple(item.version.revision for item in outcomes) == (1, 2, 3, 4, 5)
    assert duplicate.created is False
    assert len(repository.history(date(2026, 7, 22))) == 5
    assert targets.report_dates == (date(2026, 7, 21), date(2026, 7, 22))
