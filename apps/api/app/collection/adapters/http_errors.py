"""Safe HTTP failure classification for collection control flow."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final, override

HTTP_TOO_MANY_REQUESTS: Final = 429


class HttpFailureKind(StrEnum):
    """Control-plane outcomes for source HTTP failures."""

    RETRYABLE = "retryable"
    TERMINAL = "terminal"
    POLICY = "policy"
    QUOTA = "quota"


class TransportFailure(StrEnum):
    """Transport failures that carry no HTTP status."""

    TIMEOUT = "timeout"
    NETWORK = "network"


@dataclass(frozen=True, slots=True)
class HttpFailure:
    """Redacted wire failure inputs used by the classifier."""

    status_code: int | None = None
    retry_after_header: str | None = None
    rate_reset_header: str | None = None
    transport: TransportFailure | None = None


@dataclass(frozen=True, slots=True)
class ExponentialBackoff:
    """Bounded retry parameters matching the reviewed command retry windows."""

    base_seconds: Decimal = Decimal(30)
    multiplier: Decimal = Decimal(4)
    max_seconds: Decimal = Decimal(300)
    jitter_ratio: Decimal = Decimal("0.5")
    jitter_cap_seconds: Decimal = Decimal(30)
    max_attempts: int = 3

    def delay_seconds(self, retry_number: int, jitter_unit: Decimal) -> Decimal:
        """Return a deterministic delay for a caller-supplied unit jitter."""
        if retry_number < 1 or retry_number >= self.max_attempts:
            raise BackoffParameterError(parameter="retry_number")
        if jitter_unit < 0 or jitter_unit > 1:
            raise BackoffParameterError(parameter="jitter_unit")
        exponential = self.base_seconds * self.multiplier ** (retry_number - 1)
        bounded = min(exponential, self.max_seconds)
        jitter = min(bounded * self.jitter_ratio, self.jitter_cap_seconds)
        return min(bounded + jitter * jitter_unit, self.max_seconds)


@dataclass(frozen=True, slots=True)
class BackoffParameterError(Exception):
    """Invalid retry input supplied by the collection controller."""

    parameter: str

    @override
    def __str__(self) -> str:
        """Return the invalid parameter without exposing request data."""
        return f"invalid backoff parameter: {self.parameter}"


@dataclass(frozen=True, slots=True)
class HttpFailureClassification:
    """Typed failure decision without response content or credentials."""

    kind: HttpFailureKind
    code: str
    retry_after_seconds: Decimal | None
    backoff: ExponentialBackoff | None


@dataclass(frozen=True, slots=True)
class AdapterHttpError(Exception):
    """Redacted adapter exception safe for control-plane persistence."""

    classification: HttpFailureClassification
    status_code: int | None
    request_path: str

    @override
    def __str__(self) -> str:
        """Return the redacted status and reviewed request path."""
        status = "transport" if self.status_code is None else str(self.status_code)
        return f"{self.classification.code}: {status}: {self.request_path}"


def classify_http_failure(failure: HttpFailure) -> HttpFailureClassification:
    """Classify status and transport failures without inspecting response bodies."""
    retry_after = _nonnegative_decimal(
        failure.retry_after_header or failure.rate_reset_header
    )
    status = failure.status_code
    if status is None:
        return HttpFailureClassification(
            kind=HttpFailureKind.RETRYABLE,
            code=(failure.transport or TransportFailure.NETWORK).value,
            retry_after_seconds=None,
            backoff=ExponentialBackoff(),
        )
    if status in {401, 403}:
        return HttpFailureClassification(
            kind=HttpFailureKind.POLICY,
            code="provider_authorization_rejected",
            retry_after_seconds=None,
            backoff=None,
        )
    if status == HTTP_TOO_MANY_REQUESTS:
        return HttpFailureClassification(
            kind=HttpFailureKind.QUOTA,
            code="provider_quota_exhausted",
            retry_after_seconds=retry_after,
            backoff=None,
        )
    if status in {408, 425, 500, 502, 503, 504}:
        return HttpFailureClassification(
            kind=HttpFailureKind.RETRYABLE,
            code="provider_temporarily_unavailable",
            retry_after_seconds=retry_after,
            backoff=ExponentialBackoff(),
        )
    return HttpFailureClassification(
        kind=HttpFailureKind.TERMINAL,
        code="provider_terminal_http_error",
        retry_after_seconds=None,
        backoff=None,
    )


def _nonnegative_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    return parsed if parsed >= 0 else None
