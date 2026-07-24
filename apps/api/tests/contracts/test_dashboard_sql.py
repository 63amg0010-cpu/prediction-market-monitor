from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domain.enums import Country, RunStatus
from app.services.dashboard.models import OutcomeStatus
from app.services.dashboard.sql_dashboard_statements import (
    DASHBOARD_METRICS,
    SOURCE_EVIDENCE,
)
from app.services.dashboard.sql_read_statements import REPORT_PAGE
from app.services.dashboard.sql_rows import SourceRow


def test_metrics_sql_only_binds_latest_valid_analysis() -> None:
    # Given: the production PostgreSQL dashboard aggregate statement.
    statement = str(DASHBOARD_METRICS)

    # When: the analysis binding predicate is inspected as a SQL contract.
    join = statement.split("LEFT JOIN ranked_analysis a", maxsplit=1)[1].split(
        "LEFT JOIN analysis_queue", maxsplit=1
    )[0]

    # Then: current post versions count only the latest explicitly valid result.
    assert "a.post_version_id = cp.current_version_id" in join
    assert "a.row_number = 1" in join
    assert "a.state::text = 'valid'" in join


def test_source_sql_selects_latest_attempt_even_when_it_failed() -> None:
    # Given: the production PostgreSQL source-evidence statement.
    statement = str(SOURCE_EVIDENCE)

    # When: the latest-attempt lateral relation is isolated.
    latest_attempt = statement.split("LEFT JOIN LATERAL (", maxsplit=1)[1].split(
        ") latest_attempt ON true", maxsplit=1
    )[0]

    # Then: ordering is over every attempt, without a success-only predicate.
    assert "WHERE r.source_id = s.id" in latest_attempt
    assert "r.status::text = 'succeeded'" not in latest_attempt
    assert "ORDER BY COALESCE(r.finished_at, r.started_at, r.created_at) DESC" in (
        latest_attempt
    )


def test_newer_failed_attempt_prevents_success_projection() -> None:
    # Given: a recent success followed by a newer terminal failure.
    generated_at = datetime(2026, 7, 22, 3, tzinfo=UTC)
    latest_success = generated_at - timedelta(minutes=20)
    row = SourceRow(
        source_id=UUID(int=1),
        display_name="source",
        country=Country.US,
        enabled=True,
        latest_attempt_status=RunStatus.FAILED_TERMINAL,
        latest_attempt_finished_at=generated_at - timedelta(minutes=5),
        latest_successful_run_at=latest_success,
        visible_publication_sequence=4,
        failure_code="provider_contract_changed",
    )

    # When: the source card projection is derived.
    projected = row.projection(generated_at)

    # Then: the newer failure is visible while success provenance remains retained.
    assert projected.status is OutcomeStatus.ERROR
    assert projected.latest_successful_run_at == latest_success
    assert projected.failure_code == "provider_contract_changed"
    assert projected.retry_eligible is False
    assert projected.retry_block_reason == "collection_terminal"


def test_report_sql_loads_only_retained_artifacts_needed_for_reproduction() -> None:
    statement = str(REPORT_PAGE)

    assert "JOIN report_input_manifests m" in statement
    assert "m.compressed_payload AS compressed_manifest_payload" in statement
    assert "m.manifest_payload_sha256" in statement
    assert "v.report_payload_sha256" in statement
    assert "FROM posts" not in statement
    assert "FROM analyses" not in statement
