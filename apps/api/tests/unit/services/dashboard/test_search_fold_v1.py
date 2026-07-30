from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
from app.services.dashboard.filters import (
    SearchFoldError,
    search_fold_v1,
    search_like_pattern_v1,
)
from pydantic import BaseModel, ConfigDict


class ValidVector(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    input: str
    folded_value: str
    scalar_count: int
    server_like_pattern: str


class InvalidVector(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    input: str
    utf16_code_units: tuple[int, ...] | None = None
    reason: str

    def search_input(self) -> str:
        if self.utf16_code_units is None:
            return self.input
        return "".join(chr(code_unit) for code_unit in self.utf16_code_units)


class VectorFixture(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    version: str
    valid_vectors: tuple[ValidVector, ...]
    invalid_inputs: tuple[InvalidVector, ...]


_VECTORS_PATH = (
    Path(__file__).resolve().parents[6] / "contracts" / "search-fold-v1-vectors.json"
)
_VECTORS = VectorFixture.model_validate_json(_VECTORS_PATH.read_bytes())


@pytest.mark.parametrize(
    "vector",
    _VECTORS.valid_vectors,
    ids=[vector.name for vector in _VECTORS.valid_vectors],
)
def test_search_fold_v1_matches_shared_vectors(vector: ValidVector) -> None:
    # Given: a versioned cross-language input and its independent expected values.
    # When: the API folds and counts the user search.
    result = search_fold_v1(vector.input)

    # Then: normalization, ASCII fold, and scalar count match the fixture.
    assert result.value == vector.folded_value
    assert result.scalar_count == vector.scalar_count


@pytest.mark.parametrize(
    "vector",
    _VECTORS.valid_vectors,
    ids=[vector.name for vector in _VECTORS.valid_vectors],
)
def test_search_like_pattern_v1_matches_server_vectors(vector: ValidVector) -> None:
    # Given: a folded value containing possible SQL wildcard characters.
    folded = search_fold_v1(vector.input)

    # When: the server alone builds the LIKE pattern.
    pattern = search_like_pattern_v1(folded.value)

    # Then: escaping happens once in backslash, percent, underscore order.
    assert pattern == vector.server_like_pattern


@pytest.mark.parametrize(
    "vector",
    _VECTORS.invalid_inputs,
    ids=[vector.name for vector in _VECTORS.invalid_inputs],
)
def test_search_fold_v1_rejects_invalid_vectors(vector: InvalidVector) -> None:
    # Given: a malformed or out-of-bound shared search input.
    # When / Then: the boundary rejects it with the fixture's stable reason.
    with pytest.raises(SearchFoldError) as captured:
        _ = search_fold_v1(vector.search_input())
    assert captured.value.reason == vector.reason
