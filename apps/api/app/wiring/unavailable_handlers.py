"""Fail-closed operation handlers for surfaces without production stores."""

# ruff: noqa: D102 - Method contracts are documented by their route Protocols.

from __future__ import annotations

from typing import TYPE_CHECKING

from anyio.lowlevel import checkpoint

from .unavailable_identity import raise_unavailable

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID

    from app.api.routes.commands import (
        CollectionRetryCommand,
        CommandAccepted,
        DailyReconcileCommand,
    )
    from app.api.routes.cron import DailyCronResponse
    from app.api.routes.verification import (
        ObservationAccepted,
        VerificationObservationPayload,
        VerificationSnapshot,
    )
    from app.api.routes.worker import (
        WorkerAckPayload,
        WorkerHeartbeatPayload,
        WorkerHeartbeatResult,
        WorkerLeaseRequest,
        WorkerLeaseResponse,
    )
    from app.collection.checkpoint import CheckpointReplay
    from app.collection.command_store import (
        ClaimOperation,
        ClaimResult,
        ConfirmOperation,
        ReserveOperation,
    )
    from app.collection.commands import CommandState
    from app.collection.completion_models import CompletionOutcome
    from app.collection.completion_store import CompletionOperation
    from app.collection.dispatch import ClaimCredentials
    from app.collection.page_commit import PageCommitOutcome
    from app.collection.page_service_models import PageCommitOperation
    from app.collection.skip_decision_models import (
        SkipDecisionOperation,
        SkipDecisionOutcome,
    )
    from app.collection.slot_store import MaterializationOperation
    from app.services.dashboard.filters import (
        DashboardFilters,
        PostFilters,
        ReportFilters,
    )
    from app.services.dashboard.models import (
        AuthorizedService,
        DashboardResponse,
        PostPage,
        ReportItem,
        ReportPage,
    )


class UnavailableDashboardReader:
    """Reject reads instead of fabricating empty dashboard projections."""

    async def dashboard(self, filters: DashboardFilters) -> DashboardResponse:
        del filters
        await checkpoint()
        raise_unavailable()

    async def posts(self, filters: PostFilters) -> PostPage:
        del filters
        await checkpoint()
        raise_unavailable()

    async def reports(self, filters: ReportFilters) -> ReportPage:
        del filters
        await checkpoint()
        raise_unavailable()

    async def report(self, report_date: date) -> ReportItem | None:
        del report_date
        await checkpoint()
        raise_unavailable()


class UnavailableCollectionRepository:
    """Reject collector work instead of acknowledging unpersisted state."""

    async def materialize(
        self, operation: MaterializationOperation
    ) -> tuple[UUID, ...]:
        del operation
        await checkpoint()
        raise_unavailable()

    async def reserve(self, operation: ReserveOperation) -> CommandState:
        del operation
        await checkpoint()
        raise_unavailable()

    async def confirm(self, operation: ConfirmOperation) -> CommandState:
        del operation
        await checkpoint()
        raise_unavailable()

    async def claim(self, operation: ClaimOperation) -> ClaimResult:
        del operation
        await checkpoint()
        raise_unavailable()

    async def checkpoint(self, run_id: UUID) -> CheckpointReplay:
        del run_id
        await checkpoint()
        raise_unavailable()

    async def commit_page(self, operation: PageCommitOperation) -> PageCommitOutcome:
        del operation
        await checkpoint()
        raise_unavailable()

    async def heartbeat(
        self, command_id: UUID, credentials: ClaimCredentials
    ) -> CommandState:
        del command_id, credentials
        await checkpoint()
        raise_unavailable()

    async def complete(self, operation: CompletionOperation) -> CompletionOutcome:
        del operation
        await checkpoint()
        raise_unavailable()

    async def attach_skip_decision(
        self, operation: SkipDecisionOperation
    ) -> SkipDecisionOutcome:
        del operation
        await checkpoint()
        raise_unavailable()


class UnavailableAdminCommandHandler:
    """Reject operator commands until an idempotent durable handler exists."""

    async def retry_collection(
        self, command: CollectionRetryCommand
    ) -> CommandAccepted:
        del command
        await checkpoint()
        raise_unavailable()

    async def reconcile_daily(self, command: DailyReconcileCommand) -> CommandAccepted:
        del command
        await checkpoint()
        raise_unavailable()


class UnavailableVerificationHandler:
    """Reject verifier work until its transactional repository exists."""

    async def snapshot(self) -> VerificationSnapshot:
        await checkpoint()
        raise_unavailable()

    async def record(
        self, payload: VerificationObservationPayload
    ) -> ObservationAccepted:
        del payload
        await checkpoint()
        raise_unavailable()


class UnavailableWorkerHandler:
    """Reject leases rather than returning synthetic queue outcomes."""

    async def lease(
        self, principal: AuthorizedService, request: WorkerLeaseRequest
    ) -> WorkerLeaseResponse:
        del principal, request
        await checkpoint()
        raise_unavailable()

    async def heartbeat(
        self, principal: AuthorizedService, payload: WorkerHeartbeatPayload
    ) -> WorkerHeartbeatResult:
        del principal, payload
        await checkpoint()
        raise_unavailable()

    async def ack(
        self, principal: AuthorizedService, payload: WorkerAckPayload
    ) -> None:
        del principal, payload
        await checkpoint()
        raise_unavailable()


class UnavailableDailyCronHandler:
    """Reject cron work until report and retention repositories are durable."""

    async def run_daily(self) -> DailyCronResponse:
        await checkpoint()
        raise_unavailable()


__all__ = [
    "UnavailableAdminCommandHandler",
    "UnavailableCollectionRepository",
    "UnavailableDailyCronHandler",
    "UnavailableDashboardReader",
    "UnavailableVerificationHandler",
    "UnavailableWorkerHandler",
]
