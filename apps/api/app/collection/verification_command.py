"""Independent GitHub Actions verification command."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.api.routes.verification import (
    VerificationObservationPayload,
    VerificationSnapshot,
)

from .cadence_result import (
    CadenceOperationResult,
    CadenceSourceResult,
    result_hash,
    write_result,
)
from .cli_config import json_bytes, required
from .control_plane_client import ControlPlaneClient
from .verification import SourceVerificationFacts, derive_source_result

if TYPE_CHECKING:
    from collections.abc import Mapping


async def verify(environment: Mapping[str, str]) -> None:
    """Fetch one no-store snapshot and record its expected verifier slot."""
    scope = required(environment, "MONITOR_SCOPE_VERSION")
    started_at = datetime.now(UTC)
    slot_value = environment.get("MONITOR_CADENCE_SLOT_KEY")
    slot = (
        datetime.fromisoformat(slot_value)
        if slot_value
        else started_at.replace(
            minute=started_at.minute - started_at.minute % 15,
            second=0,
            microsecond=0,
        )
    )
    async with ControlPlaneClient(
        required(environment, "MONITOR_API_URL"), environment
    ) as client:
        token = await client.exchange_github_oidc()
        response = await client.authorized_request(
            "GET", "/v1/verification/snapshot", token
        )
        snapshot = VerificationSnapshot.model_validate_json(response.content)
        payload = observation(snapshot, scope, slot, started_at)
        _ = await client.authorized_request(
            "POST",
            "/v1/verification/observations",
            token,
            content=json_bytes(payload.model_dump(mode="json")),
        )
        output = environment.get("MONITOR_CADENCE_RESULT_PATH")
        if output:
            completed_at = datetime.now(UTC)
            write_result(
                output,
                CadenceOperationResult(
                    schedule_kind="verifier",
                    slot_key=slot.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    started_at=started_at.isoformat(),
                    completed_at=completed_at.isoformat(),
                    source_results=tuple(
                        CadenceSourceResult(
                            source_id=item.source_id,
                            succeeded=item.status.value == "passed",
                            receipt_sha256=result_hash(item),
                        )
                        for item in payload.source_results
                    ),
                ),
            )


def observation(
    snapshot: VerificationSnapshot,
    scope: str,
    slot: datetime,
    started_at: datetime,
) -> VerificationObservationPayload:
    """Derive the client observation bound to exact snapshot evidence."""
    results = tuple(
        derive_source_result(
            SourceVerificationFacts(
                source.source_id,
                source.enabled,
                snapshot.published_at,
                source.latest_successful_run_id,
                source.latest_successful_run_finished_at,
                source.visible_publication_manifest_id,
                source.visible_publication_sequence,
                source.publication_first_visible_at,
            ),
            slot,
            started_at,
        )
        for source in snapshot.sources
    )
    return VerificationObservationPayload(
        scope_version=scope,
        expected_slot_utc=slot,
        action_started_at=started_at,
        snapshot_id=snapshot.snapshot_id,
        snapshot_checksum=snapshot.checksum,
        source_results=results,
    )


__all__ = ("observation", "verify")
