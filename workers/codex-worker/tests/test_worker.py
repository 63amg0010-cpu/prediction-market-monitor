from monitor_worker.capability import CapabilityApproved, local_capability_decision
from monitor_worker.exchange import ExchangeToken
from monitor_worker.worker import (
    WorkerBlocked,
    WorkerDependencies,
    WorkLease,
    run_once,
)


class ForbiddenExchange:
    def exchange(self) -> ExchangeToken:
        message = "blocked worker must not read credentials or exchange"
        raise AssertionError(message)


class ForbiddenQueue:
    def lease(self, token: ExchangeToken) -> WorkLease | None:
        del token
        message = "blocked worker must not claim or lease"
        raise AssertionError(message)

    def acknowledge(
        self, token: ExchangeToken, lease: WorkLease, output: bytes
    ) -> None:
        del token, lease, output
        message = "blocked worker must not acknowledge"
        raise AssertionError(message)


class ForbiddenRunner:
    def run(self, permit: CapabilityApproved, content: str) -> bytes:
        del permit, content
        message = "blocked worker must not invoke Codex"
        raise AssertionError(message)


def test_local_worker_blocks_before_credentials_queue_or_codex() -> None:
    # Given
    dependencies = WorkerDependencies(
        exchange=ForbiddenExchange(),
        queue=ForbiddenQueue(),
        runner=ForbiddenRunner(),
    )

    # When
    result = run_once(local_capability_decision(), dependencies)

    # Then
    assert isinstance(result, WorkerBlocked)
    assert result.capability_status == "blocked_capability"
    assert result.worker_enabled is False
    assert result.worker_may_claim_or_lease is False
    assert result.alternate_model_fallback == "none"
    assert "zero_tools_unproven" in result.reason_codes
