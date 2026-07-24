from uuid import UUID

from app.domain.enums import ManifestCodec, ReportRole, Sentiment
from app.reporting.formula import project_report
from app.services.dashboard.models import ReproductionStatus
from app.services.dashboard.report_rows import ReportRow

from .factories import NOW, manifest_payload, record, valid_analysis


def _report_row() -> ReportRow:
    payload = manifest_payload(
        (
            record(
                1,
                ReportRole.PRIMARY,
                valid_analysis(
                    1,
                    relevance=True,
                    sentiment=Sentiment.POSITIVE,
                ),
            ),
        )
    )
    build = project_report(payload)
    report = build.payload
    envelope = build.manifest.envelope
    return ReportRow(
        id=UUID(int=100),
        report_date_seoul=report.report_date_seoul,
        revision=1,
        status=report.status,
        candidate_count=report.candidate_count,
        relevant_count=report.relevant_count,
        pending_count=report.pending_count,
        analysis_coverage=report.analysis_coverage_decimal,
        comments_sum=report.comments_sum,
        score_sum=report.score_sum,
        highlights=report.highlights,
        rising_keywords=report.rising_keywords,
        source_coverage=report.source_coverage,
        manifest_id=UUID(int=200),
        input_set_hash=report.input_set_hash,
        manifest_payload_sha256=report.manifest_payload_sha256,
        report_payload_sha256=build.payload_sha256,
        manifest_input_set_hash=envelope.input_set_hash,
        manifest_report_date_seoul=report.report_date_seoul,
        manifest_report_revision=1,
        manifest_codec=ManifestCodec.GZIP,
        compressed_manifest_payload=envelope.compressed_payload,
        manifest_uncompressed_byte_length=envelope.uncompressed_byte_length,
        report_payload=build.canonical_bytes,
        created_at=NOW,
    )


def test_report_projection_marks_byte_equal_retained_reproduction_verified() -> None:
    row = _report_row()

    projected = row.projection()

    assert projected.reproduction_status is ReproductionStatus.VERIFIED
    assert projected.input_set_hash == row.input_set_hash
    assert projected.manifest_payload_sha256 == row.manifest_payload_sha256
    assert projected.report_payload_sha256 == row.report_payload_sha256


def test_report_projection_marks_identity_mismatch_unverified() -> None:
    row = _report_row().model_copy(update={"manifest_report_revision": 2})

    assert row.projection().reproduction_status is ReproductionStatus.UNVERIFIED


def test_report_projection_marks_stored_payload_corruption_unverified() -> None:
    row = _report_row().model_copy(update={"report_payload": b"{}"})

    assert row.projection().reproduction_status is ReproductionStatus.UNVERIFIED


def test_report_projection_marks_manifest_corruption_unverified() -> None:
    row = _report_row().model_copy(
        update={"compressed_manifest_payload": b"not-a-gzip-manifest"}
    )

    assert row.projection().reproduction_status is ReproductionStatus.UNVERIFIED
