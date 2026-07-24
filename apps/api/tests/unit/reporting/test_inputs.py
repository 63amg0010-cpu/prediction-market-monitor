from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.domain.enums import AnalysisState, Country, ReportRole, SourcePlatform
from app.reporting.coverage import CollectionStatus, SourceCoverage, ratio_decimal
from app.reporting.inputs import AnalysisSnapshot, EngagementSnapshot
from pydantic import ValidationError

NOW = datetime(2026, 7, 22, tzinfo=UTC)
HASH = "a" * 64


def test_analysis_state_preserves_missing_values_instead_of_inventing_neutral() -> None:
    # Given: blocked analysis with every semantic value explicitly null.
    blocked = {
        "state": AnalysisState.BLOCKED_CAPABILITY,
        "analysis_id": None,
        "output_hash": None,
        "prompt_version": None,
        "model_version": None,
        "schema_version": None,
        "analyzed_at": None,
        "relevance": None,
        "sentiment": None,
    }

    # When: the retained analysis snapshot is parsed.
    snapshot = AnalysisSnapshot.model_validate(blocked)

    # Then: missing analysis remains missing and cannot carry relevance.
    assert snapshot.relevance is None
    with pytest.raises(ValidationError):
        _ = AnalysisSnapshot.model_validate({**blocked, "relevance": False})


def test_engagement_selected_values_remain_independently_nullable() -> None:
    # Given: a selected observation where comments were unavailable.
    selected = {
        "selection_state": "selected",
        "observation_id": UUID(int=1),
        "engagement_hash": HASH,
        "observed_at": NOW,
        "comments_count": None,
        "upvote_or_score": 0,
    }

    # When: the engagement snapshot is parsed.
    snapshot = EngagementSnapshot.model_validate(selected)

    # Then: unknown comments differ from an observed score of zero.
    assert snapshot.comments_count is None
    assert snapshot.upvote_or_score == 0


def test_source_coverage_requires_exact_counts_and_decimal() -> None:
    # Given: one complete source with two valid analyses among three candidates.
    values = {
        "role": ReportRole.PRIMARY,
        "source_id": UUID(int=2),
        "country": Country.US,
        "platform": SourcePlatform.REDDIT,
        "community": "r/Polymarket",
        "expected": True,
        "enabled": True,
        "collection_status": CollectionStatus.COMPLETE,
        "expected_run_count": 1,
        "successful_run_count": 1,
        "failed_run_count": 0,
        "skipped_run_count": 0,
        "candidate_count": 3,
        "valid_analysis_count": 2,
        "pending_count": 1,
        "relevant_count": 1,
        "cutoff_publication_sequence": None,
        "cutoff_publication_manifest_id": None,
        "cutoff_publication_manifest_hash": None,
        "latest_successful_run_started_at": NOW,
        "latest_successful_run_finished_at": NOW,
        "latest_publication_committed_at": NOW,
        "latest_attempt_finished_at": NOW,
        "status_observed_at": NOW,
        "coverage_numerator": 2,
        "coverage_denominator": 3,
        "coverage_decimal": ratio_decimal(2, 3),
    }

    # When: coverage is parsed and one scalar is then corrupted.
    coverage = SourceCoverage.model_validate(values)

    # Then: the exact ratio is retained and mismatched pending count is rejected.
    assert coverage.coverage_decimal == "0.6666666666666666666666666667"
    with pytest.raises(ValidationError):
        _ = SourceCoverage.model_validate({**values, "pending_count": 0})
