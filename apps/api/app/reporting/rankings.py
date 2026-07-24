"""Deterministic category and phrase ranking formulas."""

from collections import Counter
from fractions import Fraction
from typing import assert_never

from app.domain.enums import Sentiment

from .coverage import ratio_decimal
from .inputs import ReportRecord
from .manifest_schema import FormulaConstants
from .report_schema import Highlight, RisingKeyword


def _sentiment_weight(sentiment: Sentiment | None) -> int:
    match sentiment:
        case Sentiment.POSITIVE:
            return 1
        case Sentiment.NEUTRAL | None:
            return 0
        case Sentiment.NEGATIVE:
            return -1
        case _:
            assert_never(sentiment)


def _category_counts(records: tuple[ReportRecord, ...]) -> Counter[str]:
    return Counter(
        category for record in records for category in record.effective_categories
    )


def rank_highlights(
    primary: tuple[ReportRecord, ...],
    comparison: tuple[ReportRecord, ...],
    constants: FormulaConstants,
) -> tuple[Highlight, ...]:
    """Rank at most five primary categories by count, net sentiment, and name."""
    primary_counts = _category_counts(primary)
    comparison_counts = _category_counts(comparison)
    net = Counter[str]()
    for record in primary:
        weight = _sentiment_weight(record.analysis.sentiment)
        for category in record.effective_categories:
            net[category] += weight
    ordered = sorted(
        primary_counts,
        key=lambda category: (-primary_counts[category], -net[category], category),
    )[: constants.highlight_limit]
    return tuple(
        Highlight(
            category=category,
            primary_count=primary_counts[category],
            comparison_count=comparison_counts[category],
            delta=primary_counts[category] - comparison_counts[category],
            delta_rate_numerator=primary_counts[category] - comparison_counts[category],
            delta_rate_denominator=comparison_counts[category],
            delta_rate_decimal=ratio_decimal(
                primary_counts[category] - comparison_counts[category],
                comparison_counts[category],
            ),
            net_sentiment=net[category],
        )
        for category in ordered
    )


def _phrase_counts(records: tuple[ReportRecord, ...]) -> Counter[str]:
    counts = Counter[str]()
    for record in records:
        phrases = {
            item.normalized_phrase for item in record.rule_matches if item.match_present
        }
        counts.update(phrases)
    return counts


def rank_rising_keywords(
    primary: tuple[ReportRecord, ...],
    comparison: tuple[ReportRecord, ...],
    constants: FormulaConstants,
) -> tuple[RisingKeyword, ...]:
    """Threshold, rate-rank, and limit normalized phrases deterministically."""
    primary_counts = _phrase_counts(primary)
    comparison_counts = _phrase_counts(comparison)
    eligible = tuple(
        phrase
        for phrase, count in primary_counts.items()
        if count >= constants.rising_keyword_min_primary_count
    )
    rated = sorted(
        (phrase for phrase in eligible if comparison_counts[phrase] > 0),
        key=lambda phrase: (
            -Fraction(
                primary_counts[phrase] - comparison_counts[phrase],
                comparison_counts[phrase],
            ),
            -primary_counts[phrase],
            phrase,
        ),
    )
    unrated = sorted(
        (phrase for phrase in eligible if comparison_counts[phrase] == 0),
        key=lambda phrase: (-primary_counts[phrase], phrase),
    )
    ordered = (*rated, *unrated)[: constants.rising_keyword_limit]
    return tuple(
        RisingKeyword(
            phrase=phrase,
            primary_count=primary_counts[phrase],
            comparison_count=comparison_counts[phrase],
            delta=primary_counts[phrase] - comparison_counts[phrase],
            delta_rate_numerator=primary_counts[phrase] - comparison_counts[phrase],
            delta_rate_denominator=comparison_counts[phrase],
            delta_rate_decimal=ratio_decimal(
                primary_counts[phrase] - comparison_counts[phrase],
                comparison_counts[phrase],
            ),
        )
        for phrase in ordered
    )
