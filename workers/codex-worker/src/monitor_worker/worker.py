"""Fail-closed worker orchestration."""

from dataclasses import dataclass
from typing import Literal, Protocol, assert_never
from uuid import UUID

from .capability import CapabilityApproved, CapabilityBlocked, CapabilityDecision
from .exchange import ExchangeToken


@dataclass(frozen=True, slots=True)
class WorkLease:
    """Worker-visible content bound to one immutable analysis tuple."""

    item_id: UUID
    post_version_id: UUID
    content_hash: str
    prompt_version: str
    model_version: str
    schema_version: str
    lease_token: str
    content: str


class TokenExchange(Protocol):
    """Approved worker token exchange operation."""

    def exchange(self) -> ExchangeToken:
        """Return a short-lived exact-scope token."""
        ...


class QueueClient(Protocol):
    """Typed lease and acknowledgement boundary."""

    def lease(self, token: ExchangeToken) -> WorkLease | None:
        """Lease at most one exact-bound queue item."""
        ...

    def acknowledge(
        self, token: ExchangeToken, lease: WorkLease, output: bytes
    ) -> None:
        """CAS-acknowledge strict output for the same lease."""
        ...


class IsolatedRunner(Protocol):
    """Runner callable only with an approved capability permit."""

    def run(self, permit: CapabilityApproved, content: str) -> bytes:
        """Return strict JSON bytes without a fallback model."""
        ...


@dataclass(frozen=True, slots=True)
class WorkerDependencies:
    """External operations skipped entirely for a blocked worker."""

    exchange: TokenExchange
    queue: QueueClient
    runner: IsolatedRunner


@dataclass(frozen=True, slots=True)
class WorkerBlocked:
    """Observable disabled state required by the Phase 0 proof."""

    reason_codes: tuple[str, ...]
    capability_status: Literal["blocked_capability"] = "blocked_capability"
    worker_enabled: Literal[False] = False
    worker_may_claim_or_lease: Literal[False] = False
    alternate_model_fallback: Literal["none"] = "none"


@dataclass(frozen=True, slots=True)
class WorkerIdle:
    """Approved worker state with no available queue item."""

    status: Literal["idle"] = "idle"


@dataclass(frozen=True, slots=True)
class WorkerSucceeded:
    """One analysis acknowledged through its original lease."""

    item_id: UUID
    status: Literal["succeeded"] = "succeeded"


type WorkerRunResult = WorkerBlocked | WorkerIdle | WorkerSucceeded


def run_once(
    decision: CapabilityDecision, dependencies: WorkerDependencies
) -> WorkerRunResult:
    """Stop before credentials and queue access unless every proof passed."""
    match decision:  # noqa: RUF100  # noqa: MATCH_OK
        case CapabilityBlocked(reason_codes=reasons):
            return WorkerBlocked(reason_codes=reasons)
        case CapabilityApproved() as permit:
            token = dependencies.exchange.exchange()
            lease = dependencies.queue.lease(token)
            if lease is None:
                return WorkerIdle()
            output = dependencies.runner.run(permit, lease.content)
            dependencies.queue.acknowledge(token, lease, output)
            return WorkerSucceeded(item_id=lease.item_id)
    assert_never(decision)
