"""Immutable policy used to assemble database report inputs."""

from dataclasses import dataclass
from typing import assert_never, override

from app.domain.enums import SourcePlatform
from app.services.configuration.canonical import canonical_sha256
from app.services.configuration.models import ReviewedConfiguration, SourceProvider

from .manifest_schema import (
    AnalysisVersionTuple,
    CategoryMappingSnapshot,
    FormulaConstants,
    GoverningDefinitions,
    RuleSetVersion,
)

FORMULA_VERSION = "daily-report-formula-v1"


@dataclass(frozen=True, slots=True)
class ReportAssemblyError(Exception):
    """Reject report assembly when durable facts contradict policy."""

    reason: str

    @override
    def __str__(self) -> str:
        """Return the stable redacted reason code."""
        return self.reason


@dataclass(frozen=True, slots=True)
class ReportAssemblyPolicy:
    """Versioned definitions and authoritative category mappings for SQL input."""

    source_scope_version: str
    definitions: GoverningDefinitions
    mappings: tuple[CategoryMappingSnapshot, ...]
    default_category: str
    expected_sources: frozenset[tuple[SourcePlatform, str]]

    def rule_mapping(
        self, normalized_phrase: str, rule_set_version: str
    ) -> CategoryMappingSnapshot:
        """Resolve one exact versioned rule mapping."""
        selected = tuple(
            item
            for item in self.mappings
            if item.input_kind == "rule"
            and item.normalized_value == normalized_phrase
            and item.version == rule_set_version
        )
        if len(selected) != 1:
            reason = "report_rule_mapping_missing"
            raise ReportAssemblyError(reason)
        return selected[0]

    def topic_mapping(
        self, normalized_topic: str, schema_version: str
    ) -> CategoryMappingSnapshot:
        """Resolve one topic mapping with the configured fallback."""
        configured = next(
            (
                item
                for item in self.mappings
                if item.input_kind == "topic"
                and item.normalized_value == normalized_topic
                and item.version == schema_version
            ),
            None,
        )
        if configured is not None:
            return configured
        return CategoryMappingSnapshot(
            input_kind="topic",
            rule_or_topic_key=normalized_topic,
            version=schema_version,
            normalized_value=normalized_topic,
            category=self.default_category,
        )


def report_assembly_policy(
    configuration: ReviewedConfiguration,
    analysis_versions: tuple[AnalysisVersionTuple, ...],
) -> ReportAssemblyPolicy:
    """Compile reviewed configuration into report input policy."""
    constants = FormulaConstants(
        complete_coverage_numerator=85,
        complete_coverage_denominator=100,
        highlight_limit=5,
        rising_keyword_limit=10,
        rising_keyword_min_primary_count=3,
        zero_denominator="null",
        missing_analysis_semantics="excluded_not_neutral",
    )
    category_by_rule = {
        item.input_key: item
        for item in configuration.categories.mappings
        if item.input_kind == "rule"
    }
    rule_sets: list[RuleSetVersion] = []
    mappings: list[CategoryMappingSnapshot] = []
    for rule_set in configuration.keywords.rule_sets:
        rule_sets.append(
            RuleSetVersion(
                rule_set_id=rule_set.rule_set_id,
                version=rule_set.version,
                rules_hash=rule_set.canonical_sha256,
            )
        )
        for rule in rule_set.rules:
            category = category_by_rule.get(rule.rule_id)
            if category is None:
                reason = "report_rule_category_missing"
                raise ReportAssemblyError(reason)
            mappings.append(
                CategoryMappingSnapshot(
                    input_kind="rule",
                    rule_or_topic_key=rule.rule_id,
                    version=rule_set.version,
                    normalized_value=rule.normalized_phrase,
                    category=category.category,
                )
            )
    for category in configuration.categories.mappings:
        if category.input_kind == "topic":
            mappings.extend(
                CategoryMappingSnapshot(
                    input_kind="topic",
                    rule_or_topic_key=category.input_key,
                    version=version.schema_version,
                    normalized_value=category.normalized_value,
                    category=category.category,
                )
                for version in analysis_versions
            )
    return ReportAssemblyPolicy(
        source_scope_version=configuration.sources.scope_version,
        definitions=GoverningDefinitions(
            formula_version=FORMULA_VERSION,
            formula_hash=canonical_sha256(constants),
            constants=constants,
            metric_version=configuration.metrics.version,
            metric_hash=configuration.metrics.canonical_sha256,
            category_version=configuration.categories.version,
            category_hash=configuration.categories.canonical_sha256,
            rule_sets=tuple(rule_sets),
            analysis_versions=analysis_versions,
        ),
        mappings=tuple(mappings),
        default_category=configuration.categories.default_category,
        expected_sources=frozenset(
            (_source_platform(item.provider), item.source_id)
            for item in configuration.sources.sources
            if item.enabled
        ),
    )


def _source_platform(provider: SourceProvider) -> SourcePlatform:
    match provider:
        case SourceProvider.REDDIT:
            return SourcePlatform.REDDIT
        case SourceProvider.DCINSIDE:
            return SourcePlatform.DCINSIDE
        case SourceProvider.TOSS:
            return SourcePlatform.TOSS_SECURITIES
        case SourceProvider.NAVER_FINANCE:
            return SourcePlatform.NAVER_FINANCE
        case _:
            assert_never(provider)
