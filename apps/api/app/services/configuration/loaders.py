"""YAML trust-boundary loaders and cross-document invariants."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import yaml

from .errors import ConfigurationParseError, invariant
from .models import (
    AuthorizationDecision,
    AuthorizationStatus,
    ReviewedCategories,
    ReviewedConfiguration,
    ReviewedKeywords,
    ReviewedMetrics,
    ReviewedSources,
    SourceProvider,
)
from .yaml_adapter import YamlValue
from .yaml_adapter import load as load_yaml

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel

type YamlDocument = dict[str, YamlValue]


SOURCES_FILE: Final[str] = "sources.reviewed.yml"
KEYWORDS_FILE: Final[str] = "keywords.reviewed.yml"
CATEGORIES_FILE: Final[str] = "report-categories.v1.yml"
METRICS_FILE: Final[str] = "metrics.v1.yml"
REDDIT_CANDIDATES: Final[frozenset[str]] = frozenset(
    {"r/Polymarket", "r/Kalshi", "r/PredictionMarkets"}
)


def _read_yaml(path: Path) -> YamlDocument:
    try:
        loaded = load_yaml(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, ValueError) as error:
        raise ConfigurationParseError(str(path), str(error)) from error
    if not isinstance(loaded, dict):
        raise ConfigurationParseError(
            str(path), "the YAML root must be a string-keyed mapping"
        )
    return loaded


def _parse(model: type[BaseModel], raw: YamlDocument) -> BaseModel:
    return model.model_validate(raw)


def parse_sources_config(
    raw: YamlDocument, as_of: datetime | None = None
) -> ReviewedSources:
    """Parse and fail closed on source scope, authority and exclusivity."""
    parsed = _parse(ReviewedSources, raw)
    if not isinstance(parsed, ReviewedSources):
        invariant("configuration_type_invalid", "sources", "unexpected parser result")
    now = datetime.now(UTC) if as_of is None else _aware(as_of)
    reddit = next(
        (
            source
            for source in parsed.sources
            if source.provider is SourceProvider.REDDIT
        ),
        None,
    )
    if reddit is None or frozenset(reddit.scope.subreddits) != REDDIT_CANDIDATES:
        invariant(
            "reddit_scope_invalid",
            "sources.reddit.scope.subreddits",
            "the reviewed Reddit candidates are fixed",
        )
    source_ids = {source.source_id for source in parsed.sources}
    if not {"toss", "naver_finance"}.issubset(source_ids):
        invariant(
            "exclusive_sources_missing",
            "sources",
            "Toss and Naver entries are required",
        )
    for source in parsed.sources:
        authorization = source.authorization
        if authorization.status is AuthorizationStatus.APPROVED:
            _validate_approved(source.source_id, source.enabled, authorization, now)
        elif source.enabled:
            invariant(
                "source_authorization_missing",
                f"sources.{source.source_id}",
                "enabled sources require approved authorization",
            )
        if (
            source.scope.reviewed_route is not None
            and source.scope.reviewed_route not in authorization.permitted_routes
        ):
            invariant(
                "route_not_authorized",
                f"sources.{source.source_id}.scope.reviewed_route",
                "route is not in the authorization decision",
            )
    return parsed


def _validate_approved(
    source_id: str, enabled: bool, decision: AuthorizationDecision, as_of: datetime
) -> None:
    if not decision.evidence:
        invariant(
            "authorization_evidence_missing",
            f"sources.{source_id}.authorization.evidence",
            "approval evidence is required",
        )
    if decision.expires_at is None or decision.expires_at <= as_of:
        invariant(
            "authorization_expired",
            f"sources.{source_id}.authorization.expires_at",
            "approval is expired",
        )
    if enabled and (decision.effective_at is None or decision.effective_at > as_of):
        invariant(
            "authorization_not_effective",
            f"sources.{source_id}.authorization.effective_at",
            "approval is not effective",
        )
    if (
        not decision.permitted_methods
        or not decision.permitted_routes
        or not decision.purpose
    ):
        invariant(
            "authorization_scope_missing",
            f"sources.{source_id}.authorization",
            "approval requires route, method and purpose",
        )


def parse_keywords_config(raw: YamlDocument) -> ReviewedKeywords:
    """Parse immutable bilingual keyword rules."""
    parsed = _parse(ReviewedKeywords, raw)
    if not isinstance(parsed, ReviewedKeywords):
        invariant("configuration_type_invalid", "keywords", "unexpected parser result")
    return parsed


def parse_categories_config(raw: YamlDocument) -> ReviewedCategories:
    """Parse immutable category definitions and mappings."""
    parsed = _parse(ReviewedCategories, raw)
    if not isinstance(parsed, ReviewedCategories):
        invariant(
            "configuration_type_invalid", "categories", "unexpected parser result"
        )
    return parsed


def parse_metrics_config(raw: YamlDocument) -> ReviewedMetrics:
    """Parse UTC, Seoul and nullable metric semantics."""
    parsed = _parse(ReviewedMetrics, raw)
    if not isinstance(parsed, ReviewedMetrics):
        invariant("configuration_type_invalid", "metrics", "unexpected parser result")
    return parsed


def load_sources_config(path: Path, as_of: datetime | None = None) -> ReviewedSources:
    """Load and parse the reviewed source YAML file."""
    return parse_sources_config(_read_yaml(path), as_of)


def load_keywords_config(path: Path) -> ReviewedKeywords:
    """Load and parse the reviewed keyword YAML file."""
    return parse_keywords_config(_read_yaml(path))


def load_categories_config(path: Path) -> ReviewedCategories:
    """Load and parse the reviewed category YAML file."""
    return parse_categories_config(_read_yaml(path))


def load_metrics_config(path: Path) -> ReviewedMetrics:
    """Load and parse the reviewed metrics YAML file."""
    return parse_metrics_config(_read_yaml(path))


def load_all_configurations(
    config_dir: Path, as_of: datetime | None = None
) -> ReviewedConfiguration:
    """Load all documents and require every rule to have a category mapping."""
    sources = load_sources_config(config_dir / SOURCES_FILE, as_of)
    keywords = load_keywords_config(config_dir / KEYWORDS_FILE)
    categories = load_categories_config(config_dir / CATEGORIES_FILE)
    metrics = load_metrics_config(config_dir / METRICS_FILE)
    mapping_keys = {
        (mapping.input_kind, mapping.input_key) for mapping in categories.mappings
    }
    missing = {
        ("rule", rule.rule_id)
        for rule_set in keywords.rule_sets
        for rule in rule_set.rules
        if ("rule", rule.rule_id) not in mapping_keys
    }
    if missing:
        invariant(
            "keyword_category_missing",
            "categories.mappings",
            f"missing mappings: {sorted(missing)}",
        )
    return ReviewedConfiguration(
        sources=sources, keywords=keywords, categories=categories, metrics=metrics
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        invariant("timestamp_timezone_missing", "as_of", "as_of must include timezone")
    return value.astimezone(UTC)
