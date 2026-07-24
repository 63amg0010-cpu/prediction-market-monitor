"""Canonical value-complete report input manifests."""

from __future__ import annotations

import gzip
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Literal, assert_never, override

from app.domain.enums import AnalysisState, ManifestCodec, ReportRole
from app.services.configuration.canonical import canonical_bytes

from .manifest_schema import (
    AnalysisVersionTuple,
    CategoryMappingSnapshot,
    FormulaConstants,
    GoverningDefinitions,
    ReportInputManifest,
    RuleSetVersion,
)
from .windows import seoul_report_windows

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .inputs import ReportRecord

__all__ = [
    "AnalysisVersionTuple",
    "CategoryMappingSnapshot",
    "FormulaConstants",
    "GoverningDefinitions",
    "ReportInputManifest",
    "RuleSetVersion",
]

type CategoryMappingKey = tuple[Literal["rule", "topic"], str, str, str]


@dataclass(frozen=True, slots=True)
class ManifestEnvelope:
    """Persisted compressed bytes and uncompressed identity metadata."""

    codec: ManifestCodec
    compressed_payload: bytes
    uncompressed_byte_length: int
    manifest_payload_sha256: str
    input_set_hash: str


@dataclass(frozen=True, slots=True)
class ManifestBuild:
    """Normalized payload paired with its deterministic persisted envelope."""

    payload: ReportInputManifest
    envelope: ManifestEnvelope
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class ManifestIntegrityError(Exception):
    """Typed fail-closed error for corrupt or noncanonical retained input."""

    reason: str

    @override
    def __str__(self) -> str:
        """Return the stable manifest failure reason code."""
        return self.reason


def _role_rank(role: ReportRole) -> int:
    match role:
        case ReportRole.PRIMARY:
            return 0
        case ReportRole.COMPARISON:
            return 1
        case _:
            assert_never(role)


def _normalize_record(
    record: ReportRecord,
    ordinal: int,
    mappings: Mapping[CategoryMappingKey, CategoryMappingSnapshot],
) -> ReportRecord:
    rules = tuple(
        sorted(
            record.rule_matches,
            key=lambda item: (
                item.normalized_phrase,
                item.rule_set_version,
                str(item.match_id),
            ),
        )
    )
    topics = tuple(
        sorted(
            record.topic_matches,
            key=lambda item: (
                item.normalized_value,
                item.analysis_schema_version,
                item.topic_key,
            ),
        )
    )
    categories: tuple[str, ...] = ()
    if record.analysis.state is AnalysisState.VALID and record.analysis.relevance:
        mapped = {
            mappings[
                (
                    "rule",
                    item.rule_id,
                    item.rule_set_version,
                    item.normalized_phrase,
                )
            ].category
            for item in rules
            if item.match_present
        }
        mapped.update(
            mappings[
                (
                    "topic",
                    item.topic_key,
                    item.analysis_schema_version,
                    item.normalized_value,
                )
            ].category
            for item in topics
        )
        categories = tuple(sorted(mapped or {"uncategorized"}))
    return record.model_copy(
        update={
            "ordinal": ordinal,
            "rule_matches": rules,
            "topic_matches": topics,
            "effective_categories": categories,
        }
    )


def normalize_manifest(payload: ReportInputManifest) -> ReportInputManifest:
    """Derive every canonical array order and record ordinal."""
    mapping_by_key: dict[CategoryMappingKey, CategoryMappingSnapshot] = {
        (
            item.input_kind,
            item.rule_or_topic_key,
            item.version,
            item.normalized_value,
        ): item
        for item in payload.category_mappings
    }
    ordered_records: list[ReportRecord] = []
    for role in (ReportRole.PRIMARY, ReportRole.COMPARISON):
        records = sorted(
            (item for item in payload.records if item.role is role),
            key=lambda item: (str(item.post_version_id), str(item.source_id)),
        )
        ordered_records.extend(
            _normalize_record(item, ordinal, mapping_by_key)
            for ordinal, item in enumerate(records)
        )
    definitions = payload.definitions.model_copy(
        update={
            "rule_sets": tuple(
                sorted(
                    payload.definitions.rule_sets,
                    key=lambda item: (item.rule_set_id, item.version, item.rules_hash),
                )
            ),
            "analysis_versions": tuple(
                sorted(
                    payload.definitions.analysis_versions,
                    key=lambda item: (
                        item.prompt_version,
                        item.model_version,
                        item.schema_version,
                    ),
                )
            ),
        }
    )
    mappings = tuple(
        sorted(
            payload.category_mappings,
            key=lambda item: (
                item.input_kind,
                item.normalized_value,
                item.version,
                item.rule_or_topic_key,
            ),
        )
    )
    coverage = tuple(
        sorted(
            payload.source_coverage,
            key=lambda item: (_role_rank(item.role), str(item.source_id)),
        )
    )
    windows = seoul_report_windows(payload.report_date_seoul)
    return payload.model_copy(
        update={
            "windows": (windows.primary, windows.comparison),
            "definitions": definitions,
            "category_mappings": mappings,
            "records": tuple(ordered_records),
            "source_coverage": coverage,
        }
    )


def build_manifest(payload: ReportInputManifest) -> ManifestBuild:
    """Build deterministic gzip storage and both uncompressed hashes."""
    normalized = normalize_manifest(payload)
    serialized = canonical_bytes(normalized)
    payload_hash = sha256(serialized).hexdigest()
    input_hash = sha256(b"report-input-manifest/v1\n" + serialized).hexdigest()
    envelope = ManifestEnvelope(
        codec=ManifestCodec.GZIP,
        compressed_payload=gzip.compress(serialized, compresslevel=9, mtime=0),
        uncompressed_byte_length=len(serialized),
        manifest_payload_sha256=payload_hash,
        input_set_hash=input_hash,
    )
    return ManifestBuild(normalized, envelope, serialized)


def read_manifest(envelope: ManifestEnvelope) -> ReportInputManifest:
    """Verify, parse, and require canonical ordering from retained bytes only."""
    match envelope.codec:
        case ManifestCodec.GZIP:
            try:
                serialized = gzip.decompress(envelope.compressed_payload)
            except (EOFError, OSError) as error:
                reason = "manifest_decompression_failed"
                raise ManifestIntegrityError(reason) from error
        case _:
            assert_never(envelope.codec)
    payload_hash = sha256(serialized).hexdigest()
    input_hash = sha256(b"report-input-manifest/v1\n" + serialized).hexdigest()
    if (
        len(serialized) != envelope.uncompressed_byte_length
        or payload_hash != envelope.manifest_payload_sha256
        or input_hash != envelope.input_set_hash
    ):
        reason = "manifest_identity_mismatch"
        raise ManifestIntegrityError(reason)
    try:
        payload = ReportInputManifest.model_validate_json(serialized)
    except ValueError as error:
        reason = "manifest_schema_invalid"
        raise ManifestIntegrityError(reason) from error
    if canonical_bytes(normalize_manifest(payload)) != serialized:
        reason = "manifest_not_canonical"
        raise ManifestIntegrityError(reason)
    return payload
