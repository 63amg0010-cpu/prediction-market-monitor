"""Complete collector workflow command orchestration."""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .cadence_result import (
    CadenceOperationResult,
    CadenceSourceResult,
    result_hash,
    write_result,
)
from .cli_config import (
    command_secrets,
    optional_uuid,
    positive_int,
    required,
    source_ids,
    system_clock,
    utc_datetime,
)
from .collector_sources import source_executions
from .collector_workflow import CollectionInvocation, run_collection_workflow
from .control_plane_client import ControlPlaneClient

EXPECTED_CADENCE_SOURCES = 2

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from .collector_workflow import (
        CollectorControlPlane,
        CommandSecrets,
        SourceExecution,
    )
    from .completion_models import CompletionResponse


async def execute_collect_command(
    environment: Mapping[str, str],
    control: CollectorControlPlane,
    sources: tuple[SourceExecution, ...],
    secret_factory: Callable[[], CommandSecrets],
    clock: Callable[[], datetime],
) -> tuple[CompletionResponse, ...]:
    """Execute the complete collection state machine used by the CLI."""
    invocation = CollectionInvocation(
        scope_version=required(environment, "MONITOR_SCOPE_VERSION"),
        deployment_activation_at=utc_datetime(
            required(environment, "MONITOR_DEPLOYMENT_ACTIVATION_AT")
        ),
        source_ids=source_ids(environment),
        github_run_id=required(environment, "GITHUB_RUN_ID"),
        github_run_attempt=positive_int(environment, "GITHUB_RUN_ATTEMPT"),
        command_id=optional_uuid(environment.get("MONITOR_COMMAND_ID")),
    )
    return await run_collection_workflow(
        control, invocation, sources, secret_factory, clock
    )


async def collect(environment: Mapping[str, str]) -> None:
    """Construct managed clients and run one bounded collection invocation."""
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            ControlPlaneClient(required(environment, "MONITOR_API_URL"), environment)
        )
        sources = await source_executions(environment, stack, system_clock)
        started_at = datetime.now(UTC)
        completions = await execute_collect_command(
            environment,
            client,
            sources,
            command_secrets,
            system_clock,
        )
        output = environment.get("MONITOR_CADENCE_RESULT_PATH")
        if output:
            publications = tuple(
                publication
                for completion in completions
                for publication in completion.publications
            )
            by_source = {item.source_id: item for item in publications}
            expected = source_ids(environment)
            if (
                len(publications) != EXPECTED_CADENCE_SOURCES
                or set(by_source) != set(expected)
            ):
                message = "cadence_collection_result_source_set_mismatch"
                raise ValueError(message)
            write_result(
                output,
                CadenceOperationResult(
                    schedule_kind="collection",
                    slot_key=required(environment, "MONITOR_CADENCE_SLOT_KEY"),
                    started_at=started_at.isoformat(),
                    completed_at=datetime.now(UTC).isoformat(),
                    source_results=tuple(
                        CadenceSourceResult(
                            source_id=source_id,
                            succeeded=True,
                            receipt_sha256=result_hash(by_source[source_id]),
                        )
                        for source_id in expected
                    ),
                ),
            )


__all__ = ("collect", "execute_collect_command")
