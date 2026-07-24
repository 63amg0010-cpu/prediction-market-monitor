import pytest
from app.analysis.output import AnalysisOutput, Sentiment
from pydantic import ValidationError


def test_strict_output_accepts_relevant_sentiment_and_unique_topics() -> None:
    # Given
    raw = b'{"relevance":true,"sentiment":"positive","topics":["rates","fed"]}'

    # When
    output = AnalysisOutput.model_validate_json(raw)

    # Then
    assert output.sentiment is Sentiment.POSITIVE
    assert output.topics == ("rates", "fed")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"relevance":false,"sentiment":"neutral","topics":[]}',
        b'{"relevance":"true","sentiment":"positive","topics":[]}',
        b'{"relevance":true,"sentiment":"positive","topics":[],"extra":1}',
        b'{"relevance":true,"sentiment":"positive","topics":["fed","fed"]}',
    ],
)
def test_strict_output_rejects_invalid_or_extra_values(raw: bytes) -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        _ = AnalysisOutput.model_validate_json(raw)
