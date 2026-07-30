"""GitHub OIDC client for one schema-closed cadence workflow result."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import urlencode

import anyio
import httpx2
from app.collection.cadence_result import CadenceOperationResult

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def scheduled_slot(
    kind: Literal["collection", "verifier"], now: datetime
) -> str:
    """Return the latest exact due slot; server timing still decides credit."""
    value = now.astimezone(UTC).replace(second=0, microsecond=0)
    if kind == "verifier":
        value = value.replace(minute=value.minute - value.minute % 15)
    else:
        candidate = value.replace(hour=value.hour - value.hour % 3, minute=17)
        value = candidate if candidate <= value else candidate - timedelta(hours=3)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        message = f"cadence_workflow_{name.lower()}_missing"
        raise ValueError(message)
    return value


class Arguments(argparse.Namespace):
    """Typed parser target for both client subcommands."""

    command: str = ""
    kind: Literal["collection", "verifier"] = "collection"
    api_url: str = ""
    epoch_id: str = ""
    mode: Literal["schedule", "retry", "manual"] = "manual"
    cadence_attempt: int = 1
    failed_predecessor_attempt_id: str = "none"
    result: str = ""
    json_out: str = ""


async def record(args: Arguments, environment: Mapping[str, str]) -> int:
    result = CadenceOperationResult.model_validate_json(
        await anyio.Path(args.result).read_bytes()
    )
    workflow = "collect.yml" if result.schedule_kind == "collection" else "verify.yml"
    expected_environment = (
        "production-collector"
        if result.schedule_kind == "collection"
        else "production-verifier"
    )
    request_url = _required(environment, "ACTIONS_ID_TOKEN_REQUEST_URL")
    separator = "&" if "?" in request_url else "?"
    async with httpx2.AsyncClient(timeout=10) as client:
        token_response = await client.get(
            f"{request_url}{separator}{urlencode({'audience': 'monitor-control'})}",
            headers={
                "Authorization": (
                    "Bearer "
                    + _required(environment, "ACTIONS_ID_TOKEN_REQUEST_TOKEN")
                )
            },
        )
        _ = token_response.raise_for_status()
        oidc = cast("dict[str, object]", token_response.json()).get("value")
        if not isinstance(oidc, str) or not oidc:
            message = "cadence_workflow_oidc_value_invalid"
            raise ValueError(message)
        payload = {
            "repository": _required(environment, "GITHUB_REPOSITORY"),
            "workflow": workflow,
            "head_sha": _required(environment, "GITHUB_SHA"),
            "ref": _required(environment, "GITHUB_REF"),
            "event": _required(environment, "GITHUB_EVENT_NAME"),
            "environment": expected_environment,
            "run_id": int(_required(environment, "GITHUB_RUN_ID")),
            "run_attempt": int(_required(environment, "GITHUB_RUN_ATTEMPT")),
            "epoch_id": args.epoch_id,
            "schedule_kind": result.schedule_kind,
            "slot_key": result.slot_key,
            "workflow_mode": args.mode,
            "cadence_attempt": args.cadence_attempt,
            "failed_predecessor_attempt_id": (
                None
                if args.failed_predecessor_attempt_id == "none"
                else args.failed_predecessor_attempt_id
            ),
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "source_results": [
                item.model_dump(mode="json") for item in result.source_results
            ],
        }
        response = await client.post(
            args.api_url.rstrip("/") + "/internal/release/cadence-workflow-attempt",
            headers={
                "Authorization": f"Bearer {oidc}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        _ = response.raise_for_status()
    receipt = cast("dict[str, object]", response.json())
    if (
        receipt.get("schema") != "cadence-workflow-attempt-receipt.v1"
        or receipt.get("recorded") is not True
        or (
            args.mode in {"schedule", "retry"}
            and receipt.get("cadence_accepted") is not True
        )
    ):
        message = "cadence_workflow_receipt_rejected"
        raise ValueError(message)
    _ = await anyio.Path(args.json_out).write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    slot = commands.add_parser("slot")
    _ = slot.add_argument("--kind", choices=("collection", "verifier"), required=True)
    submit = commands.add_parser("record")
    _ = submit.add_argument("--api-url", required=True)
    _ = submit.add_argument("--epoch-id", required=True)
    _ = submit.add_argument(
        "--mode", choices=("schedule", "retry", "manual"), required=True
    )
    _ = submit.add_argument(
        "--cadence-attempt", type=int, choices=(1, 2), required=True
    )
    _ = submit.add_argument("--failed-predecessor-attempt-id", required=True)
    _ = submit.add_argument("--result", required=True)
    _ = submit.add_argument("--json-out", required=True)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    """Resolve a slot or submit one authenticated operation result."""
    args = Arguments()
    _ = parser().parse_args(argv, namespace=args)
    if args.command == "slot":
        _ = sys.stdout.write(scheduled_slot(args.kind, datetime.now(UTC)) + "\n")
        return 0
    return anyio.run(record, args, os.environ)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("main", "scheduled_slot")
