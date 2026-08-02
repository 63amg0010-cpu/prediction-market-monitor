"""CLI adapter for the transactional dispatch-reserve command."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import create_async_engine

from .dispatch_reservations import (
    DispatchReserveRequest,
    reserve_dispatch,
    write_reservation,
)

MAX_OPERATION_INPUT_KEY = 64
MAX_OPERATION_INPUT_VALUE = 512

if TYPE_CHECKING:
    import argparse


class DispatchReserveArgs(Protocol):
    database_url_env: str
    repository: str
    workflow: str
    display_title: str
    head_sha: str
    expected_plan_sha256: str
    activation_nonce: str
    dispatch_nonce: str
    predecessor_receipt: str
    attempt: int
    json_out: str
    git_ref: str
    operation_input: list[str]


class SubparserRegistry(Protocol):
    """Minimal argparse subparser surface used by the release gate."""

    def add_parser(self, name: str) -> argparse.ArgumentParser:
        """Add and return one named command parser."""
        ...


def add_dispatch_reserve_parser(
    subcommands: SubparserRegistry,
) -> None:
    """Register the stable pre-dispatch reservation argv contract."""
    command = subcommands.add_parser("dispatch-reserve")
    for name in (
        "database-url-env",
        "repository",
        "workflow",
        "display-title",
        "head-sha",
        "expected-plan-sha256",
        "activation-nonce",
        "dispatch-nonce",
        "predecessor-receipt",
        "json-out",
    ):
        _ = command.add_argument(f"--{name}", required=True)
    _ = command.add_argument("--attempt", required=True, type=int)
    _ = command.add_argument("--ref", dest="git_ref", default="refs/heads/main")
    _ = command.add_argument("--operation-input", action="append", default=[])


async def execute_dispatch_reserve(args: DispatchReserveArgs) -> int:
    """Append one reservation using only the named database URL environment."""
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        error_code = "database_url_environment_empty"
        raise ValueError(error_code)
    request = dispatch_reserve_request(args)
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            receipt = await reserve_dispatch(connection, request)
    finally:
        await engine.dispose()
    write_reservation(request.json_out, receipt)
    return 0


def dispatch_reserve_request(args: DispatchReserveArgs) -> DispatchReserveRequest:
    """Build the runtime Pydantic request from exact parsed CLI fields."""
    return DispatchReserveRequest(
        repository=args.repository,
        workflow=args.workflow,
        display_title=args.display_title,
        head_sha=args.head_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=UUID(args.activation_nonce),
        dispatch_nonce=UUID(args.dispatch_nonce),
        predecessor_receipt=Path(args.predecessor_receipt),
        attempt=args.attempt,
        json_out=Path(args.json_out),
        git_ref=args.git_ref,
        operation_inputs=_parse_operation_inputs(args.operation_input),
    )


def _parse_operation_inputs(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in values:
        key, separator, value = item.partition("=")
        if (
            separator != "="
            or not key
            or len(key) > MAX_OPERATION_INPUT_KEY
            or not key.replace("_", "a").isalnum()
            or key in result
            or not value
            or len(value) > MAX_OPERATION_INPUT_VALUE
            or any(character.isspace() for character in key)
        ):
            error_code = "operation_input_invalid"
            raise ValueError(error_code)
        result[key] = value
    return result


__all__ = (
    "add_dispatch_reserve_parser",
    "dispatch_reserve_request",
    "execute_dispatch_reserve",
)
