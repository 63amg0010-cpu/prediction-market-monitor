"""Bounded GitHub REST workflow-run selection and stabilization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from urllib.parse import quote

from app.domain.types import JsonValue
from pydantic import TypeAdapter, ValidationError

from scripts.release_dispatch_contracts import (
    ChildRunner,
    JsonObject,
    canonical_bytes,
    hold,
    run_once,
)

ACCEPT = "Accept: application/vnd.github+json"
API_VERSION = "X-GitHub-Api-Version: 2022-11-28"
MAX_PAGES = 10
PER_PAGE = 100
MAX_POLLS = 24
_JSON_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """All immutable fields needed to correlate one claimed workflow run."""

    repository: str
    workflow: str
    display_title: str
    head_sha: str
    activation_nonce: str
    dispatch_nonce: str
    attempt: int
    selection_floor_at: str
    claimed_run_id: int | None


def truncated_floor(value: str) -> tuple[datetime, str]:
    """Parse a timestamp and truncate it to a UTC whole-second floor."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        error_code = "selection_floor_invalid"
        raise ValueError(error_code) from error
    if parsed.tzinfo is None:
        hold("selection_floor_invalid")
    floor = parsed.astimezone(UTC).replace(microsecond=0)
    return floor, floor.isoformat().replace("+00:00", "Z")


def list_runs_argv(identity: RunIdentity, page: int) -> tuple[str, ...]:
    """Build the normative REST argv for one page."""
    _, floor = truncated_floor(identity.selection_floor_at)
    created = quote(f">={floor}", safe="")
    endpoint = (
        f"/repos/{identity.repository}/actions/workflows/{identity.workflow}/runs"
        f"?event=workflow_dispatch&created={created}&per_page={PER_PAGE}&page={page}"
    )
    return (
        "gh",
        "api",
        "--method",
        "GET",
        "-H",
        ACCEPT,
        "-H",
        API_VERSION,
        endpoint,
    )


def _page(runner: ChildRunner, identity: RunIdentity, page: int) -> JsonObject:
    result = run_once(runner, list_runs_argv(identity, page))
    try:
        value = _JSON_ADAPTER.validate_json(result.stdout)
    except ValidationError as error:
        error_code = "github_runs_json_invalid"
        raise ValueError(error_code) from error
    if not isinstance(value, dict):
        hold("github_runs_object_required")
    obj = cast("JsonObject", value)
    if not isinstance(obj.get("total_count"), int) or not isinstance(
        obj.get("workflow_runs"), list
    ):
        hold("github_runs_schema_invalid")
    return obj


def _snapshot(runner: ChildRunner, identity: RunIdentity) -> JsonObject:
    runs: list[JsonValue] = []
    expected_total: int | None = None
    for page_number in range(1, MAX_PAGES + 1):
        page = _page(runner, identity, page_number)
        total = cast("int", page["total_count"])
        if total > MAX_PAGES * PER_PAGE:
            hold("selection_window_overflow")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            hold("selection_snapshot_unstable")
        page_runs = cast("list[JsonValue]", page["workflow_runs"])
        runs.extend(page_runs)
        if len(page_runs) < PER_PAGE or len(runs) >= total:
            break
    if expected_total is None or len(runs) < min(expected_total, 1000):
        hold("selection_window_truncated")
    return {"total_count": expected_total, "workflow_runs": runs}


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        hold("run_created_at_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        error_code = "run_created_at_invalid"
        raise ValueError(error_code) from error
    if parsed.tzinfo is None:
        hold("run_created_at_invalid")
    return parsed.astimezone(UTC)


def _candidate(run: JsonValue, identity: RunIdentity) -> bool:
    if not isinstance(run, dict):
        hold("github_run_schema_invalid")
    floor, _ = truncated_floor(identity.selection_floor_at)
    value = cast("JsonObject", run)
    return (
        value.get("display_title") == identity.display_title
        and value.get("head_sha") == identity.head_sha
        and value.get("event") == "workflow_dispatch"
        and _timestamp(value.get("created_at")) >= floor
    )


def _selected(snapshot: JsonObject, identity: RunIdentity) -> JsonObject | None:
    runs = cast("list[JsonValue]", snapshot["workflow_runs"])
    matches = [run for run in runs if _candidate(run, identity)]
    if len(matches) > 1:
        hold("run_correlation_multiple")
    if not matches:
        return None
    selected = cast("JsonObject", matches[0])
    if (
        identity.claimed_run_id is not None
        and selected.get("id") != identity.claimed_run_id
    ):
        hold("run_claim_mismatch")
    required = (
        "id",
        "workflow_id",
        "display_title",
        "head_sha",
        "event",
        "created_at",
        "status",
        "conclusion",
    )
    if any(field not in selected for field in required):
        hold("github_run_schema_invalid")
    return selected


def _receipt(run: JsonObject, identity: RunIdentity, total: int) -> JsonObject:
    return {
        "databaseId": run["id"],
        "workflow_id": run["workflow_id"],
        "display_title": run["display_title"],
        "head_sha": run["head_sha"],
        "event": run["event"],
        "created_at": run["created_at"],
        "status": run["status"],
        "conclusion": run["conclusion"],
        "total_count": total,
        "activation_nonce": identity.activation_nonce,
        "dispatch_nonce": identity.dispatch_nonce,
        "attempt": identity.attempt,
    }


def select_run(
    runner: ChildRunner,
    *,
    identity: RunIdentity,
    sleep: Callable[[float], None],
) -> JsonObject:
    """Poll 24 times, then require two identical complete snapshots 10s apart."""
    for poll in range(MAX_POLLS):
        first = _snapshot(runner, identity)
        selected = _selected(first, identity)
        if selected is None:
            if poll + 1 < MAX_POLLS:
                sleep(5)
            continue
        sleep(10)
        second = _snapshot(runner, identity)
        if canonical_bytes(first) != canonical_bytes(second):
            hold("selection_snapshot_unstable")
        stable = _selected(second, identity)
        if stable is None:
            hold("selection_snapshot_unstable")
        return _receipt(stable, identity, cast("int", second["total_count"]))
    return hold("run_correlation_zero")


__all__ = ("RunIdentity", "list_runs_argv", "select_run", "truncated_floor")
