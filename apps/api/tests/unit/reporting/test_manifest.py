import gzip

import pytest
from app.domain.enums import AnalysisState, ReportRole, Sentiment
from app.reporting.coverage import CollectionStatus, SourceCoverage
from app.reporting.manifest import build_manifest, read_manifest
from app.reporting.manifest_schema import ReportInputManifest
from pydantic import ValidationError

from .factories import (
    digest,
    manifest_payload,
    missing_analysis,
    record,
    rule_match,
    valid_analysis,
)


def test_manifest_canonicalizes_pq_order_and_preserves_explicit_nulls() -> None:
    # Given: unordered Q/P records including missing analysis and null engagement.
    primary = record(2, ReportRole.PRIMARY, missing_analysis(AnalysisState.PENDING))
    comparison = record(
        1,
        ReportRole.COMPARISON,
        valid_analysis(1, relevance=True, sentiment=Sentiment.NEUTRAL),
    )

    # When: both input orders are built as canonical manifests.
    first = build_manifest(manifest_payload((primary, comparison)))
    second = build_manifest(manifest_payload((comparison, primary)))
    decoded = read_manifest(first.envelope)

    # Then: identity is order-stable and every null survives in the value payload.
    assert first.envelope.input_set_hash == second.envelope.input_set_hash
    assert gzip.decompress(first.envelope.compressed_payload) == first.canonical_bytes
    assert not first.canonical_bytes.endswith(b"\n")
    assert tuple(item.role for item in decoded.records) == (
        ReportRole.PRIMARY,
        ReportRole.COMPARISON,
    )
    assert decoded.records[0].analysis.sentiment is None
    assert decoded.records[0].engagement.comments_count is None


def test_every_primary_comparison_and_mapping_value_changes_identity() -> None:
    # Given: one relevant P and Q record with the same matched phrase.
    phrase = rule_match(1, "market", "market")
    primary = record(
        1,
        ReportRole.PRIMARY,
        valid_analysis(1, relevance=True, sentiment=Sentiment.POSITIVE),
    ).model_copy(update={"rule_matches": (phrase,)})
    comparison = record(
        2,
        ReportRole.COMPARISON,
        valid_analysis(2, relevance=True, sentiment=Sentiment.NEUTRAL),
    ).model_copy(update={"rule_matches": (phrase,)})
    base_payload = manifest_payload((primary, comparison))

    # When: a P scalar, Q scalar, or effective mapping changes independently.
    base = build_manifest(base_payload).envelope.input_set_hash
    changed_primary = primary.model_copy(
        update={
            "analysis": valid_analysis(
                1,
                relevance=True,
                sentiment=Sentiment.NEGATIVE,
            )
        }
    )
    changed_q_match = phrase.model_copy(update={"match_hash": digest(9999)})
    changed_comparison = comparison.model_copy(
        update={"rule_matches": (changed_q_match,)}
    )
    changed_mapping = base_payload.category_mappings[0].model_copy(
        update={"category": "uncategorized"}
    )
    remapped_primary = primary.model_copy(
        update={
            "rule_matches": (
                phrase.model_copy(update={"mapped_category": "uncategorized"}),
            )
        }
    )
    remapped_comparison = comparison.model_copy(
        update={
            "rule_matches": (
                phrase.model_copy(update={"mapped_category": "uncategorized"}),
            )
        }
    )
    hashes = {
        base,
        build_manifest(
            manifest_payload((changed_primary, comparison))
        ).envelope.input_set_hash,
        build_manifest(
            manifest_payload((primary, changed_comparison))
        ).envelope.input_set_hash,
        build_manifest(
            base_payload.model_copy(
                update={
                    "category_mappings": (changed_mapping,),
                    "records": (remapped_primary, remapped_comparison),
                }
            )
        ).envelope.input_set_hash,
    }

    # Then: each formula-effective change has a distinct correction identity.
    assert len(hashes) == 4


def test_manifest_rejects_record_category_that_contradicts_mapping() -> None:
    # Given: a rule record whose duplicated category disagrees with the snapshot.
    matched = rule_match(1, "market", "stale-category")
    source = record(
        1,
        ReportRole.PRIMARY,
        valid_analysis(1, relevance=True, sentiment=Sentiment.NEUTRAL),
    ).model_copy(update={"rule_matches": (matched,)})
    # When / Then: the manifest boundary rejects the stale record mapping.
    with pytest.raises(ValidationError, match="manifest_category_mapping_mismatch"):
        _ = manifest_payload((source,))


def test_manifest_rejects_missing_and_duplicate_category_mappings() -> None:
    # Given: one mapped rule record and its authoritative mapping snapshot.
    matched = rule_match(1, "market", "market")
    source = record(
        1,
        ReportRole.PRIMARY,
        valid_analysis(1, relevance=True, sentiment=Sentiment.NEUTRAL),
    ).model_copy(update={"rule_matches": (matched,)})
    payload = manifest_payload((source,))
    mapping = payload.category_mappings[0]

    # When: the mapping is omitted or duplicated at the retained boundary.
    values = payload.model_dump(mode="python", by_alias=True)
    missing = values | {"category_mappings": ()}
    duplicate = values | {"category_mappings": (mapping, mapping)}

    # Then: neither ambiguous representation can become canonical report input.
    with pytest.raises(ValidationError, match="manifest_category_mapping_missing"):
        _ = ReportInputManifest.model_validate(missing)
    with pytest.raises(ValidationError, match="manifest_category_mapping_duplicate"):
        _ = ReportInputManifest.model_validate(duplicate)


def test_source_coverage_rejects_complete_with_impossible_run_counts() -> None:
    # Given: a complete source snapshot claiming 99 expected and zero successes.
    complete = manifest_payload(()).source_coverage[0]
    values = complete.model_dump(mode="python") | {
        "collection_status": CollectionStatus.COMPLETE,
        "expected_run_count": 99,
        "successful_run_count": 0,
    }

    # When / Then: the coverage boundary rejects the contradictory state.
    with pytest.raises(ValidationError, match="coverage_run_status_mismatch"):
        _ = SourceCoverage.model_validate(values)
