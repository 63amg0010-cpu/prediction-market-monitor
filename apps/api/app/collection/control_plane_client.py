"""Bounded HTTP-only client for collector and verifier workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from app.api.routes.collector import (
    CheckpointResponse,
    ClaimResponse,
    CommandResponse,
    MaterializeResponse,
)
from app.api.routes.collector_models import SkipDecisionPayload, SkipDecisionResponse

from . import control_plane_models as contracts
from .base import CollectionError
from .cli_config import json_bytes, required
from .completion_models import CompletionRequest, CompletionResponse
from .control_plane_models import CollectionErrorEnvelope, ExchangeResponse
from .control_plane_transport import ControlPlaneTransport
from .page_commit import PageCommitRequest, PageCommitResponse

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from uuid import UUID

    import httpx2

OIDC_AUDIENCE: Final = "monitor-control"
HTTP_CONFLICT: Final = 409


class ControlPlaneClient(ControlPlaneTransport):
    """Call only scoped API operations and retain tokens in memory."""

    def __init__(
        self,
        base_url: str,
        environment: Mapping[str, str],
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """Bind a bounded client to environment-provided Actions identity."""
        super().__init__(base_url, transport=transport)
        self._environment: Mapping[str, str] = environment

    async def exchange_github_oidc(self) -> str:
        """Exchange a fresh GitHub OIDC token for a short-lived API token."""
        request_url = required(self._environment, "ACTIONS_ID_TOKEN_REQUEST_URL")
        request_token = required(self._environment, "ACTIONS_ID_TOKEN_REQUEST_TOKEN")
        response = await self._request(
            "GET",
            request_url,
            headers={"Authorization": f"Bearer {request_token}"},
            params={"audience": OIDC_AUDIENCE},
        )
        oidc_token = contracts.OidcResponse.model_validate_json(response.content).value
        exchange = await self._request(
            "POST",
            "/v1/service-tokens/github/exchange",
            content=json_bytes({"oidc_token": oidc_token}),
        )
        return ExchangeResponse.model_validate_json(exchange.content).access_token

    async def authenticate(self) -> None:
        """Acquire one in-memory API token for this bounded workflow run."""
        self.set_token(await self.exchange_github_oidc())

    async def materialize(
        self, scope_version: str, deployment_activation_at: datetime
    ) -> tuple[UUID, ...]:
        """Materialize all database-time due commands for one scope."""
        response = await self.collector_request(
            "POST",
            "/v1/collector/materialize",
            {
                "scope_version": scope_version,
                "deployment_activation_at": deployment_activation_at.isoformat(),
            },
        )
        return MaterializeResponse.model_validate_json(response.content).command_ids

    async def reserve(
        self, command_id: UUID, reservation_nonce: str, lease_token: str
    ) -> CommandResponse:
        """Reserve one durable command with ephemeral secrets."""
        response = await self.collector_request(
            "POST",
            f"/v1/collector/commands/{command_id}/reserve",
            {"reservation_nonce": reservation_nonce, "lease_token": lease_token},
        )
        return CommandResponse.model_validate_json(response.content)

    async def confirm(
        self,
        command_id: UUID,
        attempt: int,
        reservation_nonce: str,
        github_run_id: str,
        github_run_attempt: int,
    ) -> CommandResponse:
        """Bind the reservation to the current accepted GitHub run."""
        response = await self.collector_request(
            "POST",
            f"/v1/collector/commands/{command_id}/confirm-dispatch",
            {
                "attempt": attempt,
                "reservation_nonce": reservation_nonce,
                "github_run_id": github_run_id,
                "github_run_attempt": github_run_attempt,
            },
        )
        return CommandResponse.model_validate_json(response.content)

    async def claim(
        self,
        command_id: UUID,
        attempt: int,
        lease_token: str,
        reservation_nonce: str,
        source_ids: tuple[UUID, ...],
    ) -> ClaimResponse:
        """Claim the exact source set after server authorization checks."""
        response = await self.collector_request(
            "POST",
            f"/v1/collector/commands/{command_id}/claim",
            {
                "attempt": attempt,
                "lease_token": lease_token,
                "reservation_nonce": reservation_nonce,
                "source_ids": [str(source_id) for source_id in source_ids],
            },
        )
        return ClaimResponse.model_validate_json(response.content)

    async def checkpoint(self, run_id: UUID) -> CheckpointResponse:
        """Reload the sole persisted cursor before provider access."""
        response = await self.collector_request(
            "GET", f"/v1/collector/runs/{run_id}/checkpoint"
        )
        return CheckpointResponse.model_validate_json(response.content)

    async def commit_page(
        self, run_id: UUID, request: PageCommitRequest
    ) -> PageCommitResponse:
        """Commit one fetched page before another provider fetch may begin."""
        response = await self.collector_request(
            "POST",
            f"/v1/collector/runs/{run_id}/pages",
            request.model_dump(mode="json"),
            allow_conflict=True,
        )
        if response.status_code == HTTP_CONFLICT:
            error = CollectionErrorEnvelope.model_validate_json(response.content).error
            raise CollectionError(
                error.code,
                response.status_code,
                current_checkpoint_revision=error.current_checkpoint_revision,
                current_cursor=error.current_cursor,
                expected_page_ordinal=error.expected_page_ordinal,
                existing_commit_id=error.existing_commit_id,
            )
        return PageCommitResponse.model_validate_json(response.content)

    async def heartbeat(
        self, command_id: UUID, attempt: int, lease_token: str
    ) -> CommandResponse:
        """Refresh the command lease around bounded provider operations."""
        response = await self.collector_request(
            "POST",
            f"/v1/collector/commands/{command_id}/heartbeat",
            {"attempt": attempt, "lease_token": lease_token},
        )
        return CommandResponse.model_validate_json(response.content)

    async def complete(
        self, command_id: UUID, request: CompletionRequest
    ) -> CompletionResponse:
        """Submit server-verifiable terminal run facts exactly once."""
        response = await self.collector_request(
            "POST",
            f"/v1/collector/commands/{command_id}/complete",
            request.model_dump(mode="json", by_alias=True),
        )
        return CompletionResponse.model_validate_json(response.content)

    async def attach_skip_decision(
        self, run_id: UUID, payload: SkipDecisionPayload
    ) -> SkipDecisionResponse:
        """Submit only the redacted provider observation for server derivation."""
        response = await self.collector_request(
            "POST",
            f"/v1/collector/runs/{run_id}/skip-decision",
            payload.model_dump(mode="json"),
        )
        return SkipDecisionResponse.model_validate_json(response.content)


__all__ = ("ControlPlaneClient",)
