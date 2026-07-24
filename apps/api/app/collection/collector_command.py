"""Complete collector workflow command orchestration."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from .collector_workflow import (
        CollectorControlPlane,
        CommandSecrets,
        SourceExecution,
    )


async def execute_collect_command(
    environment: Mapping[str, str],
    control: CollectorControlPlane,
    sources: tuple[SourceExecution, ...],
    secret_factory: Callable[[], CommandSecrets],
    clock: Callable[[], datetime],
) -> None:
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
    await run_collection_workflow(control, invocation, sources, secret_factory, clock)


async def collect(environment: Mapping[str, str]) -> None:
    """Construct managed clients and run one bounded collection invocation."""
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            ControlPlaneClient(required(environment, "MONITOR_API_URL"), environment)
        )
        sources = await source_executions(environment, stack, system_clock)
        await execute_collect_command(
            environment,
            client,
            sources,
            command_secrets,
            system_clock,
        )


__all__ = ("collect", "execute_collect_command")
