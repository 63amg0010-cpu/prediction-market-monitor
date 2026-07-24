from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from app.analysis.benchmark import (
    BenchmarkCountry,
    BenchmarkError,
    BenchmarkErrorCode,
    BenchmarkOrigin,
    BenchmarkRecord,
    HumanLabel,
    LabelMethod,
    validate_benchmark_dataset,
)

NOW = datetime(2026, 7, 22, tzinfo=UTC)


def _record(index: int, *, origin: BenchmarkOrigin) -> BenchmarkRecord:
    country = BenchmarkCountry.KR if index < 200 else BenchmarkCountry.US
    relevant = index % 2 == 0
    source_number = index // 100
    label = HumanLabel(
        labeler_id=f"human-{source_number}-a",
        relevance=relevant,
        method=LabelMethod.BLIND_HUMAN,
        labeled_at=NOW,
    )
    second = label.model_copy(update={"labeler_id": f"human-{source_number}-b"})
    return BenchmarkRecord(
        record_id=UUID(int=index + 1),
        post_version_id=UUID(int=index + 1001),
        source_id=f"source-{source_number}",
        country=country,
        content_hash=f"{index + 1:064x}",
        collection_manifest_sha256=f"{index + 500:064x}",
        origin=origin,
        frozen_before_evaluation=True,
        used_for_tuning=False,
        first_label=label,
        second_label=second,
        adjudication=None,
    )


def test_benchmark_refuses_missing_dataset_file() -> None:
    # Given
    path = Path("definitely-missing-human-benchmark.json")

    # When / Then
    with pytest.raises(BenchmarkError) as raised:
        _ = validate_benchmark_dataset(path, ("source-0", "source-1"))
    assert raised.value.code is BenchmarkErrorCode.MISSING_DATA


def test_benchmark_refuses_synthetic_records_even_when_count_is_400() -> None:
    # Given
    records = tuple(
        _record(index, origin=BenchmarkOrigin.SYNTHETIC) for index in range(400)
    )

    # When / Then
    with pytest.raises(BenchmarkError) as raised:
        _ = validate_benchmark_dataset(
            records, ("source-0", "source-1", "source-2", "source-3")
        )
    assert raised.value.code is BenchmarkErrorCode.SYNTHETIC_DATA


def test_benchmark_requires_exactly_400_balanced_human_records() -> None:
    # Given
    records = tuple(
        _record(index, origin=BenchmarkOrigin.REAL_COLLECTED) for index in range(400)
    )

    # When
    dataset = validate_benchmark_dataset(
        records, ("source-0", "source-1", "source-2", "source-3")
    )

    # Then
    assert len(dataset.records) == 400
    assert dataset.country_counts == {
        BenchmarkCountry.KR: 200,
        BenchmarkCountry.US: 200,
    }

    with pytest.raises(BenchmarkError) as raised:
        _ = validate_benchmark_dataset(
            records[:-1], ("source-0", "source-1", "source-2", "source-3")
        )
    assert raised.value.code is BenchmarkErrorCode.RECORD_COUNT
