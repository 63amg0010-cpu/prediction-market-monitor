"""Public re-exports for configuration model consumers."""

from .base_models import (
    AuthorizationDecision,
    AuthorizationEvidence,
    AuthorizationStatus,
    BudgetPolicy,
    Exclusivity,
    ImmutableConfigModel,
    ReviewedSources,
    SourceDefinition,
    SourceLimits,
    SourceProvider,
    SourceScope,
    SourceState,
)
from .category_models import CategoryDefinition, CategoryMapping, ReviewedCategories
from .keyword_models import KeywordRule, KeywordRuleSet, ReviewedKeywords
from .metric_models import (
    ComparisonSemantics,
    DeltaSemantics,
    EngagementSemantics,
    ReportWindow,
    ReviewedMetrics,
)


class ReviewedConfiguration(ImmutableConfigModel):
    """Complete immutable Phase 1 configuration bundle."""

    sources: ReviewedSources
    keywords: ReviewedKeywords
    categories: ReviewedCategories
    metrics: ReviewedMetrics


__all__ = [
    "AuthorizationDecision",
    "AuthorizationEvidence",
    "AuthorizationStatus",
    "BudgetPolicy",
    "CategoryDefinition",
    "CategoryMapping",
    "ComparisonSemantics",
    "DeltaSemantics",
    "EngagementSemantics",
    "Exclusivity",
    "ImmutableConfigModel",
    "KeywordRule",
    "KeywordRuleSet",
    "ReportWindow",
    "ReviewedCategories",
    "ReviewedConfiguration",
    "ReviewedKeywords",
    "ReviewedMetrics",
    "ReviewedSources",
    "SourceDefinition",
    "SourceLimits",
    "SourceProvider",
    "SourceScope",
    "SourceState",
]
