"""Validated report input manifest schema."""

from datetime import date
from typing import Literal, LiteralString, NoReturn

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from app.domain.enums import AnalysisState, ReportRole

from .coverage import SourceCoverage
from .inputs import ImmutableReportModel, ReportRecord, Sha256Hex
from .windows import ReportWindow, seoul_report_windows


class FormulaConstants(ImmutableReportModel):
    """Literal policy constants embedded for configuration-free replay."""

    complete_coverage_numerator: Literal[85]
    complete_coverage_denominator: Literal[100]
    highlight_limit: Literal[5]
    rising_keyword_limit: Literal[10]
    rising_keyword_min_primary_count: Literal[3]
    zero_denominator: Literal["null"]
    missing_analysis_semantics: Literal["excluded_not_neutral"]


class RuleSetVersion(ImmutableReportModel):
    """Immutable rule-set identity retained by a manifest."""

    rule_set_id: str
    version: str
    rules_hash: Sha256Hex


class AnalysisVersionTuple(ImmutableReportModel):
    """Prompt, model, and output schema identity used for selection."""

    prompt_version: str
    model_version: str
    schema_version: str


class GoverningDefinitions(ImmutableReportModel):
    """All immutable formula, metric, category, rule, and analysis versions."""

    formula_version: str
    formula_hash: Sha256Hex
    constants: FormulaConstants
    metric_version: str
    metric_hash: Sha256Hex
    category_version: str
    category_hash: Sha256Hex
    rule_sets: tuple[RuleSetVersion, ...]
    analysis_versions: tuple[AnalysisVersionTuple, ...]


class CategoryMappingSnapshot(ImmutableReportModel):
    """One formula-effective rule or topic to category mapping."""

    input_kind: Literal["rule", "topic"]
    rule_or_topic_key: str
    version: str
    normalized_value: str
    category: str


class ReportInputManifest(ImmutableReportModel):
    """Canonical self-contained report-input-manifest/v1 payload."""

    schema_name: Literal["report-input-manifest/v1"] = Field(alias="schema")
    report_date_seoul: date
    windows: tuple[ReportWindow, ReportWindow]
    source_scope_version: str
    definitions: GoverningDefinitions
    category_mappings: tuple[CategoryMappingSnapshot, ...]
    records: tuple[ReportRecord, ...]
    source_coverage: tuple[SourceCoverage, ...]

    @model_validator(mode="after")
    def validate_windows_and_records(self) -> "ReportInputManifest":
        """Require exact Seoul P/Q windows, roles, dates, and unique records."""
        _validate_windows_and_publication(self)
        _validate_coverage(self)
        mappings = _validated_mappings(self.category_mappings)
        _validate_governing_records(self, mappings)
        return self


type _MappingKey = tuple[Literal["rule", "topic"], str, str, str]


def _validate_windows_and_publication(payload: ReportInputManifest) -> None:
    expected = seoul_report_windows(payload.report_date_seoul)
    expected_by_role = {
        ReportRole.PRIMARY: expected.primary,
        ReportRole.COMPARISON: expected.comparison,
    }
    actual_by_role = {window.role: window for window in payload.windows}
    if actual_by_role != expected_by_role:
        _invalid(
            "manifest_windows_invalid",
            "windows must be the exact primary and preceding Seoul days",
        )
    identities: set[tuple[ReportRole, str]] = set()
    for record in payload.records:
        window = expected_by_role[record.role]
        identity = (record.role, str(record.post_version_id))
        valid_publication = (
            window.start_utc <= record.published_at_utc < window.end_utc
            and record.published_date_seoul == window.date_seoul
        )
        if identity in identities or not valid_publication:
            _invalid(
                "manifest_record_role_invalid",
                "records must be unique and published inside their retained role",
            )
        identities.add(identity)


def _validate_coverage(payload: ReportInputManifest) -> None:
    coverage_keys = {(item.role, item.source_id) for item in payload.source_coverage}
    if len(coverage_keys) != len(payload.source_coverage):
        _invalid(
            "manifest_coverage_duplicate",
            "source coverage role and source pairs must be unique",
        )


def _validated_mappings(
    snapshots: tuple[CategoryMappingSnapshot, ...],
) -> dict[_MappingKey, CategoryMappingSnapshot]:
    mapping_keys: list[_MappingKey] = [
        (
            item.input_kind,
            item.rule_or_topic_key,
            item.version,
            item.normalized_value,
        )
        for item in snapshots
    ]
    if len(mapping_keys) != len(set(mapping_keys)):
        _invalid(
            "manifest_category_mapping_duplicate",
            "category mapping identities must be unique",
        )
    return dict(zip(mapping_keys, snapshots, strict=True))


def _validate_governing_records(
    payload: ReportInputManifest,
    mappings: dict[_MappingKey, CategoryMappingSnapshot],
) -> None:
    rule_versions = {
        (item.rule_set_id, item.version) for item in payload.definitions.rule_sets
    }
    analysis_versions = {
        (item.prompt_version, item.model_version, item.schema_version)
        for item in payload.definitions.analysis_versions
    }
    for record in payload.records:
        analysis = record.analysis
        version_tuple = (
            analysis.prompt_version,
            analysis.model_version,
            analysis.schema_version,
        )
        if (
            analysis.state is AnalysisState.VALID
            and version_tuple not in analysis_versions
        ):
            _invalid(
                "manifest_analysis_version_unknown",
                "valid analysis must use a governing version tuple",
            )
        for rule in record.rule_matches:
            if (rule.rule_set_id, rule.rule_set_version) not in rule_versions:
                _invalid(
                    "manifest_rule_version_unknown",
                    "rule match must use a governing rule-set version",
                )
            _require_mapping(
                mappings,
                (
                    "rule",
                    rule.rule_id,
                    rule.rule_set_version,
                    rule.normalized_phrase,
                ),
                rule.mapped_category,
                "record rule category contradicts the retained mapping",
            )
        for topic in record.topic_matches:
            _require_mapping(
                mappings,
                (
                    "topic",
                    topic.topic_key,
                    topic.analysis_schema_version,
                    topic.normalized_value,
                ),
                topic.mapped_category,
                "record topic category contradicts the retained mapping",
            )


def _require_mapping(
    mappings: dict[_MappingKey, CategoryMappingSnapshot],
    key: _MappingKey,
    mapped_category: str,
    mismatch_message: LiteralString,
) -> None:
    authoritative = mappings.get(key)
    if authoritative is None:
        _invalid(
            "manifest_category_mapping_missing",
            "every retained rule or topic requires an exact mapping",
        )
    if authoritative.category != mapped_category:
        _invalid("manifest_category_mapping_mismatch", mismatch_message)


def _invalid(error_code: LiteralString, message: LiteralString) -> NoReturn:
    raise PydanticCustomError(error_code, message)
