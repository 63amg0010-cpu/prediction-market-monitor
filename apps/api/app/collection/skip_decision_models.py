"""Typed skip-decision repository boundary."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.api.routes.collector_models import SkipDecisionPayload, SkipDecisionResponse


@dataclass(frozen=True, slots=True)
class SkipDecisionOperation:
    """Authenticated zero-commit observation submitted for server derivation."""

    run_id: UUID
    payload: SkipDecisionPayload
    actor_principal_id: str


@dataclass(frozen=True, slots=True)
class SkipDecisionOutcome:
    """Byte-stable first-write or replay result."""

    status_code: Literal[200, 201]
    response: SkipDecisionResponse
    response_bytes: bytes


__all__ = ("SkipDecisionOperation", "SkipDecisionOutcome")
