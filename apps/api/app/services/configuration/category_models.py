"""Immutable report category and mapping models."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base_models import ImmutableConfigModel
from .errors import invariant

_invariant = invariant


class CategoryDefinition(ImmutableConfigModel):
    """One normalized report category."""

    category_key: str = Field(min_length=1)
    label: str = Field(min_length=1)


class CategoryMapping(ImmutableConfigModel):
    """One rule or topic to category mapping."""

    input_kind: Literal["rule", "topic"]
    input_key: str = Field(min_length=1)
    normalized_value: str = Field(min_length=1)
    category: str = Field(min_length=1)


class ReviewedCategories(ImmutableConfigModel):
    """Immutable report category map with an explicit fallback."""

    schema_name: Literal["monitor.report-categories"] = Field(alias="schema")
    version: str
    canonicalization: Literal["json-sort-keys-nfc-v1"]
    review_state: Literal["reviewed_v1"]
    default_category: Literal["uncategorized"]
    categories: tuple[CategoryDefinition, ...]
    mappings: tuple[CategoryMapping, ...]

    @model_validator(mode="after")
    def validate_categories(self) -> ReviewedCategories:
        """Reject duplicate keys, unknown categories and missing fallback."""
        keys = [category.category_key for category in self.categories]
        mapping_keys = [(item.input_kind, item.input_key) for item in self.mappings]
        if len(keys) != len(set(keys)):
            _invariant(
                "duplicate_category",
                "categories",
                "category keys must be unique",
            )
        if "uncategorized" not in keys:
            _invariant(
                "uncategorized_missing",
                "categories",
                "uncategorized category is required",
            )
        if len(mapping_keys) != len(set(mapping_keys)):
            _invariant(
                "duplicate_category_mapping",
                "mappings",
                "mapping keys must be unique",
            )
        unknown = {item.category for item in self.mappings} - set(keys)
        if unknown:
            _invariant(
                "unknown_category",
                "mappings.category",
                "mappings must reference defined categories",
            )
        return self
