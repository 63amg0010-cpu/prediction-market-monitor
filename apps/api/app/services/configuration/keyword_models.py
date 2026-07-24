"""Immutable bilingual prediction-market vocabulary models."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base_models import ImmutableConfigModel
from .errors import invariant

_invariant = invariant


class KeywordRule(ImmutableConfigModel):
    """One bilingual prediction-market vocabulary rule."""

    rule_id: str = Field(min_length=1)
    phrase: str = Field(min_length=1)
    normalized_phrase: str = Field(min_length=1)
    language: Literal["ko", "en", "mixed"]
    match_type: Literal["contains", "exact"]

    @model_validator(mode="after")
    def validate_phrase(self) -> KeywordRule:
        """Require the stored phrase normalization to be deterministic."""
        if self.normalized_phrase != self.phrase.strip().casefold():
            _invariant(
                "keyword_normalization_invalid",
                f"rules.{self.rule_id}",
                "normalized phrase must be NFC/casefold phrase",
            )
        return self


class KeywordRuleSet(ImmutableConfigModel):
    """Versioned immutable group of keyword rules."""

    rule_set_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    rules: tuple[KeywordRule, ...]


class ReviewedKeywords(ImmutableConfigModel):
    """Immutable bilingual vocabulary configuration."""

    schema_name: Literal["monitor.keywords"] = Field(alias="schema")
    version: str
    canonicalization: Literal["json-sort-keys-nfc-v1"]
    review_state: Literal["reviewed_vocabulary_only"]
    rule_sets: tuple[KeywordRuleSet, ...]

    @model_validator(mode="after")
    def validate_rules(self) -> ReviewedKeywords:
        """Reject duplicate rule-set versions and normalized phrases."""
        set_keys = [(item.rule_set_id, item.version) for item in self.rule_sets]
        rules = [rule for item in self.rule_sets for rule in item.rules]
        phrases = [rule.normalized_phrase for rule in rules]
        if len(set_keys) != len(set(set_keys)):
            _invariant(
                "duplicate_rule_set",
                "rule_sets",
                "rule-set IDs and versions must be unique",
            )
        if len(phrases) != len(set(phrases)):
            _invariant(
                "duplicate_keyword_phrase",
                "rule_sets.rules",
                "normalized phrases must be unique",
            )
        return self
