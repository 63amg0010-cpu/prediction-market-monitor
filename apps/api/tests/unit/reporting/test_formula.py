from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.domain.enums import AnalysisState, ReportRole, ReportStatus, Sentiment
from app.reporting.coverage import CollectionStatus
from app.reporting.formula import ManifestCorruptError, project_report

from .factories import (
    manifest_payload,
    missing_analysis,
    record,
    rule_match,
    selected_engagement,
    valid_analysis,
)

if TYPE_CHECKING:
    from app.reporting.inputs import ReportRecord


def test_missing_analysis_and_null_engagement_never_become_zero_or_neutral() -> None:
    # Given: relevant, unknown-sentiment, pending, and irrelevant primary records.
    positive = record(
        1,
        ReportRole.PRIMARY,
        valid_analysis(1, relevance=True, sentiment=Sentiment.POSITIVE),
    ).model_copy(update={"engagement": selected_engagement(1, None, 0)})
    unknown = record(
        2,
        ReportRole.PRIMARY,
        valid_analysis(2, relevance=True, sentiment=None),
    )
    pending = record(
        3,
        ReportRole.PRIMARY,
        missing_analysis(AnalysisState.PENDING),
    )
    irrelevant = record(
        4,
        ReportRole.PRIMARY,
        valid_analysis(4, relevance=False, sentiment=None),
    )

    # When: the primary-only scalar projection is computed.
    report = project_report(
        manifest_payload((positive, unknown, pending, irrelevant))
    ).payload

    # Then: pending, unknown sentiment, unknown engagement, and observed zero differ.
    assert report.candidate_count == 4
    assert report.valid_analysis_count == 3
    assert report.pending_count == 1
    assert report.relevant_count == 2
    assert report.positive_count == 1
    assert report.neutral_count == 0
    assert report.unknown_sentiment_count == 1
    assert report.comments_sum is None
    assert report.comments_known_count == 0
    assert report.comments_unknown_count == 2
    assert report.score_sum == 0
    assert report.score_known_count == 1
    assert report.score_unknown_count == 1
    assert report.status is ReportStatus.PARTIAL


def test_highlights_limit_and_order_count_net_sentiment_then_category() -> None:
    # Given: six tied categories whose sentiment and names resolve the rank.
    category_sentiments = (
        ("zeta", Sentiment.POSITIVE),
        ("alpha", Sentiment.POSITIVE),
        ("beta", Sentiment.NEUTRAL),
        ("delta", Sentiment.NEGATIVE),
        ("epsilon", Sentiment.NEGATIVE),
    )
    records = tuple(
        record(
            index,
            ReportRole.PRIMARY,
            valid_analysis(index, relevance=True, sentiment=sentiment),
        ).model_copy(update={"rule_matches": (rule_match(index, category, category),)})
        for index, (category, sentiment) in enumerate(category_sentiments, start=10)
    )
    uncategorized = record(
        99,
        ReportRole.PRIMARY,
        valid_analysis(99, relevance=True, sentiment=Sentiment.NEUTRAL),
    )

    # When: category highlights are projected.
    report = project_report(manifest_payload((*records, uncategorized))).payload
    highlights = report.highlights

    # Then: exactly five remain in count/net/name order with fallback mapping.
    assert tuple(item.category for item in highlights) == (
        "alpha",
        "zeta",
        "beta",
        "uncategorized",
        "delta",
    )
    assert tuple(item.net_sentiment for item in highlights) == (1, 1, 0, 0, -1)


def test_rising_keywords_dedupe_threshold_rate_order_and_limit() -> None:
    # Given: eleven eligible phrases, one ineligible phrase, and a duplicate match.
    records: list[ReportRecord] = []
    phrase_counts = {"alpha": 3, "beta": 4, "zeta": 5}
    phrase_counts.update({f"extra-{index}": 3 for index in range(8)})
    seed = 100
    for phrase, count in phrase_counts.items():
        for _ in range(count):
            match = rule_match(seed, phrase, "market")
            matches = (match, match.model_copy(update={"match_id": match.match_id}))
            records.append(
                record(
                    seed,
                    ReportRole.PRIMARY,
                    valid_analysis(
                        seed,
                        relevance=True,
                        sentiment=Sentiment.NEUTRAL,
                    ),
                ).model_copy(update={"rule_matches": matches})
            )
            seed += 1
    for phrase, count in {"alpha": 1, "beta": 2}.items():
        for _ in range(count):
            records.append(
                record(
                    seed,
                    ReportRole.COMPARISON,
                    valid_analysis(
                        seed,
                        relevance=True,
                        sentiment=Sentiment.NEUTRAL,
                    ),
                ).model_copy(
                    update={"rule_matches": (rule_match(seed, phrase, "market"),)}
                )
            )
            seed += 1
    records.extend(
        record(
            seed + index,
            ReportRole.PRIMARY,
            valid_analysis(
                seed + index,
                relevance=True,
                sentiment=Sentiment.NEUTRAL,
            ),
        ).model_copy(
            update={"rule_matches": (rule_match(seed + index, "below", "market"),)}
        )
        for index in range(2)
    )

    # When: rising phrases are projected across P and Q.
    rising = project_report(manifest_payload(tuple(records))).payload.rising_keywords

    # Then: numeric rates lead, null rate is last-ranked, and only ten survive.
    assert len(rising) == 10
    assert tuple(item.phrase for item in rising[:3]) == ("alpha", "beta", "zeta")
    assert rising[0].primary_count == 3
    assert rising[0].comparison_count == 1
    assert rising[0].delta_rate_decimal == "2"
    assert rising[2].delta_rate_decimal is None
    assert all(item.phrase != "below" for item in rising)


def test_empty_complete_and_coverage_corruption_are_distinct() -> None:
    # Given: an honestly complete empty P/Q manifest.
    payload = manifest_payload(())

    # When: it is projected, then its retained primary count is corrupted.
    report = project_report(payload).payload
    primary = payload.source_coverage[0].model_copy(update={"candidate_count": 1})
    corrupted = payload.model_copy(
        update={"source_coverage": (primary, payload.source_coverage[1])}
    )

    # Then: empty is complete/null/empty while mismatched retained scalars fail closed.
    assert report.status is ReportStatus.COMPLETE
    assert report.analysis_coverage_decimal is None
    assert report.comments_sum is None
    assert report.highlights == ()
    assert report.rising_keywords == ()
    with pytest.raises(ManifestCorruptError):
        _ = project_report(corrupted)


def test_missing_primary_source_makes_an_empty_report_partial() -> None:
    # Given: empty counts but an expected primary source with missing collection.
    payload = manifest_payload(())
    primary = payload.source_coverage[0].model_copy(
        update={
            "collection_status": CollectionStatus.MISSING,
            "successful_run_count": 0,
            "cutoff_publication_sequence": None,
            "cutoff_publication_manifest_id": None,
            "cutoff_publication_manifest_hash": None,
            "latest_successful_run_started_at": None,
            "latest_successful_run_finished_at": None,
            "latest_publication_committed_at": None,
            "latest_attempt_finished_at": None,
            "status_observed_at": None,
        }
    )
    partial = payload.model_copy(
        update={"source_coverage": (primary, payload.source_coverage[1])}
    )

    # When: overall completeness is projected.
    status = project_report(partial).payload.status

    # Then: missing collection cannot be portrayed as a successful empty zero.
    assert status is ReportStatus.PARTIAL
