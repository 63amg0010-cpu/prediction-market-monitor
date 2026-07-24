"""Held-out relevance benchmark protocol."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, assert_never, override
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

BENCHMARK_RECORD_COUNT = 400
COUNTRY_RECORD_COUNT = 200
MINIMUM_SOURCE_RECORD_COUNT = 100
MINIMUM_COUNTRY_CLASS_COUNT = 25


@unique
class BenchmarkCountry(StrEnum):
    """Countries represented equally in the held-out set."""

    KR = "kr"
    US = "us"


@unique
class BenchmarkOrigin(StrEnum):
    """Input provenance classes accepted at the file boundary."""

    REAL_COLLECTED = "real_collected"
    SYNTHETIC = "synthetic"


@unique
class LabelMethod(StrEnum):
    """Label provenance classes accepted at the file boundary."""

    BLIND_HUMAN = "blind_human"
    GENERATED = "generated"


class HumanLabel(BaseModel):
    """Pseudonymous blind-labeling event without author data."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", strict=True
    )

    labeler_id: str = Field(min_length=1, max_length=100)
    relevance: bool
    method: LabelMethod
    labeled_at: datetime


class BenchmarkRecord(BaseModel):
    """One versioned held-out record and its independent human labels."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", strict=True
    )

    record_id: UUID
    post_version_id: UUID
    source_id: str = Field(min_length=1, max_length=100)
    country: BenchmarkCountry
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    collection_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin: BenchmarkOrigin
    frozen_before_evaluation: bool
    used_for_tuning: bool
    first_label: HumanLabel
    second_label: HumanLabel
    adjudication: HumanLabel | None


@dataclass(frozen=True, slots=True)
class ValidatedBenchmarkDataset:
    """Exactly 400 real, balanced, human-labeled held-out records."""

    records: tuple[BenchmarkRecord, ...]
    resolved_labels: tuple[bool, ...]
    country_counts: Mapping[BenchmarkCountry, int]
    source_counts: Mapping[str, int]


@unique
class BenchmarkErrorCode(StrEnum):
    """Stable refusal reasons that cannot be interpreted as a score."""

    MISSING_DATA = "missing_data"
    INVALID_DATA = "invalid_data"
    RECORD_COUNT = "record_count"
    SYNTHETIC_DATA = "synthetic_data"
    HUMAN_LABEL_REQUIRED = "human_label_required"
    HOLDOUT_CONTAMINATED = "holdout_contaminated"
    COUNTRY_BALANCE = "country_balance"
    SOURCE_BALANCE = "source_balance"
    CLASS_BALANCE = "class_balance"
    DUPLICATE_RECORD = "duplicate_record"
    SOURCE_SET_MISMATCH = "source_set_mismatch"


@dataclass(frozen=True, slots=True)
class BenchmarkError(Exception):
    """Typed refusal that never carries an accuracy claim."""

    code: BenchmarkErrorCode

    @override
    def __str__(self) -> str:
        return self.code.value


type BenchmarkInput = tuple[BenchmarkRecord, ...] | Path


def validate_benchmark_dataset(
    source: BenchmarkInput, enabled_source_ids: tuple[str, ...]
) -> ValidatedBenchmarkDataset:
    """Parse and enforce the complete N=400 held-out data protocol."""
    records = _load_records(source)
    if len(records) != BENCHMARK_RECORD_COUNT:
        raise BenchmarkError(BenchmarkErrorCode.RECORD_COUNT)
    if len(set(enabled_source_ids)) != len(enabled_source_ids):
        raise BenchmarkError(BenchmarkErrorCode.SOURCE_SET_MISMATCH)
    labels = _validate_record_provenance(records)
    source_counts, country_counts = _validate_balances(
        records, labels, enabled_source_ids
    )
    return ValidatedBenchmarkDataset(
        records=records,
        resolved_labels=labels,
        country_counts=MappingProxyType(dict(country_counts)),
        source_counts=MappingProxyType(dict(source_counts)),
    )


def _validate_record_provenance(
    records: tuple[BenchmarkRecord, ...],
) -> tuple[bool, ...]:
    record_ids = {record.record_id for record in records}
    version_ids = {record.post_version_id for record in records}
    if len(record_ids) != len(records) or len(version_ids) != len(records):
        raise BenchmarkError(BenchmarkErrorCode.DUPLICATE_RECORD)
    labels: list[bool] = []
    for record in records:
        _require_real_origin(record.origin)
        if not record.frozen_before_evaluation or record.used_for_tuning:
            raise BenchmarkError(BenchmarkErrorCode.HOLDOUT_CONTAMINATED)
        labels.append(_resolved_human_label(record))
    return tuple(labels)


def _validate_balances(
    records: tuple[BenchmarkRecord, ...],
    labels: tuple[bool, ...],
    enabled_source_ids: tuple[str, ...],
) -> tuple[Counter[str], Counter[BenchmarkCountry]]:
    source_counts = Counter(record.source_id for record in records)
    if set(source_counts) != set(enabled_source_ids):
        raise BenchmarkError(BenchmarkErrorCode.SOURCE_SET_MISMATCH)
    if any(
        source_counts[source_id] < MINIMUM_SOURCE_RECORD_COUNT
        for source_id in enabled_source_ids
    ):
        raise BenchmarkError(BenchmarkErrorCode.SOURCE_BALANCE)
    country_counts = Counter(record.country for record in records)
    if any(
        country_counts[country] != COUNTRY_RECORD_COUNT for country in BenchmarkCountry
    ):
        raise BenchmarkError(BenchmarkErrorCode.COUNTRY_BALANCE)
    for country in BenchmarkCountry:
        country_labels = tuple(
            label
            for record, label in zip(records, labels, strict=True)
            if record.country is country
        )
        if (
            country_labels.count(True) < MINIMUM_COUNTRY_CLASS_COUNT
            or country_labels.count(False) < MINIMUM_COUNTRY_CLASS_COUNT
        ):
            raise BenchmarkError(BenchmarkErrorCode.CLASS_BALANCE)
    return source_counts, country_counts


def _load_records(source: BenchmarkInput) -> tuple[BenchmarkRecord, ...]:
    match source:  # noqa: RUF100  # noqa: MATCH_OK
        case Path() as path:
            if not path.is_file():
                raise BenchmarkError(BenchmarkErrorCode.MISSING_DATA)
            try:
                payload = path.read_bytes()
            except OSError as error:
                raise BenchmarkError(BenchmarkErrorCode.INVALID_DATA) from error
            try:
                return TypeAdapter(tuple[BenchmarkRecord, ...]).validate_json(payload)
            except ValidationError as error:
                raise BenchmarkError(BenchmarkErrorCode.INVALID_DATA) from error
        case tuple() as records:
            return records
    assert_never(source)


def _require_real_origin(origin: BenchmarkOrigin) -> None:
    match origin:  # noqa: RUF100  # noqa: MATCH_OK
        case BenchmarkOrigin.REAL_COLLECTED:
            return
        case BenchmarkOrigin.SYNTHETIC:
            raise BenchmarkError(BenchmarkErrorCode.SYNTHETIC_DATA)
    assert_never(origin)


def _resolved_human_label(record: BenchmarkRecord) -> bool:
    if record.first_label.labeler_id == record.second_label.labeler_id:
        raise BenchmarkError(BenchmarkErrorCode.HUMAN_LABEL_REQUIRED)
    labels = (record.first_label, record.second_label)
    if record.adjudication is not None:
        labels += (record.adjudication,)
    for label in labels:
        _require_human_label(label.method)
    if record.first_label.relevance == record.second_label.relevance:
        if record.adjudication is not None:
            raise BenchmarkError(BenchmarkErrorCode.HUMAN_LABEL_REQUIRED)
        return record.first_label.relevance
    if record.adjudication is None:
        raise BenchmarkError(BenchmarkErrorCode.HUMAN_LABEL_REQUIRED)
    if record.adjudication.labeler_id in {
        record.first_label.labeler_id,
        record.second_label.labeler_id,
    }:
        raise BenchmarkError(BenchmarkErrorCode.HUMAN_LABEL_REQUIRED)
    return record.adjudication.relevance


def _require_human_label(method: LabelMethod) -> None:
    match method:  # noqa: RUF100  # noqa: MATCH_OK
        case LabelMethod.BLIND_HUMAN:
            return
        case LabelMethod.GENERATED:
            raise BenchmarkError(BenchmarkErrorCode.HUMAN_LABEL_REQUIRED)
    assert_never(method)
