"""Strict analysis output boundary."""

from __future__ import annotations

from typing import Annotated, ClassVar, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.domain.enums import Sentiment

Topic = Annotated[str, StringConstraints(min_length=1, max_length=80)]


class AnalysisOutput(BaseModel):
    """Only valid relevance, sentiment, and topic combinations."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", strict=True
    )

    relevance: bool
    sentiment: Sentiment | None
    topics: tuple[Topic, ...] = Field(max_length=20)

    @model_validator(mode="after")
    def validate_semantics(self) -> Self:
        """Reject ambiguous sentiment and non-canonical topic sets."""
        if self.relevance and self.sentiment is None:
            code = "sentiment_required"
            message = "relevant output requires sentiment"
            raise PydanticCustomError(code, message)
        if not self.relevance and self.sentiment is not None:
            code = "sentiment_for_irrelevant"
            message = "irrelevant output cannot have sentiment"
            raise PydanticCustomError(code, message)
        if len(set(self.topics)) != len(self.topics):
            code = "duplicate_topic"
            message = "topics must be unique"
            raise PydanticCustomError(code, message)
        if any(topic != topic.strip() for topic in self.topics):
            code = "topic_whitespace"
            message = "topics cannot have edge whitespace"
            raise PydanticCustomError(code, message)
        return self


__all__ = ["AnalysisOutput", "Sentiment"]
