"""Guarded disposable 0010->0011->0010 rollback rehearsal."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Never

_SCRIPT = Path(__file__).resolve()
sys.path.insert(0, str(_SCRIPT.parents[3]))
sys.path.insert(0, str(_SCRIPT.parents[1]))

from scripts.local_db_guard import (  # noqa: E402
    EXPECTED_DATABASE,
    LocalDatabaseHoldError,
    guarded_target,
)
from scripts.local_qa_evidence import command_nine_environment  # noqa: E402
from scripts.release_rollback_harness_db import RealDatabase  # noqa: E402
from scripts.release_rollback_harness_matrix import render_matrix_b  # noqa: E402
from scripts.release_rollback_harness_models import (  # noqa: E402
    Database,
    DatabaseSnapshot,
    ExternalReceipt,
    ExternalRecorder,
    HarnessReceipt,
    MatrixCommand,
    Options,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

SCHEMA = "release-rollback-harness.v1"
DATABASE_ENV = "MIGRATION_QA_DATABASE_URL"


class RollbackHarnessHoldError(RuntimeError):
    """Stable refusal without target URLs, credentials, or provider output."""


class _Args(argparse.Namespace):
    mode: str = ""
    database_url_env: str = ""
    stub_external: bool = False
    expected_sha: str = ""
    json_out: Path = Path()


class StubExternal:
    """Default recorder: it has no child, network, or token execution method."""

    def record(self, commands: list[MatrixCommand]) -> ExternalReceipt:
        """Return the closed proof that every command remained inert."""
        return {
            "commands": commands,
            "executed_count": 0,
            "mode": "stub-only",
            "network_access": False,
            "production_access": False,
        }


def parse_args(argv: Sequence[str] | None = None) -> Options:
    """Parse the fixed plan argv without accepting positional shortcuts."""
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--mode", required=True)
    _ = parser.add_argument("--database-url-env", required=True)
    _ = parser.add_argument("--stub-external", action="store_true", required=True)
    _ = parser.add_argument("--expected-sha", required=True)
    _ = parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args(argv, namespace=_Args())
    return Options(
        mode=str(args.mode),
        database_url_env=str(args.database_url_env),
        stub_external=bool(args.stub_external),
        expected_sha=str(args.expected_sha),
        json_out=args.json_out,
    )


def _hold(code: str) -> Never:
    raise RollbackHarnessHoldError(code)


def _validate(options: Options) -> str:
    if options.mode != "disposable":
        _hold("mode_not_disposable")
    if options.database_url_env != DATABASE_ENV:
        _hold("database_url_env_not_exact")
    if not options.stub_external:
        _hold("stub_external_required")
    if re.fullmatch(r"[0-9a-f]{40}", options.expected_sha) is None:
        _hold("expected_sha_invalid")
    if options.json_out.exists():
        _hold("json_out_exists")
    url = os.environ.get(DATABASE_ENV, "")
    try:
        _ = guarded_target(url, EXPECTED_DATABASE)
    except LocalDatabaseHoldError as error:
        msg = "database_guard_refused"
        raise RollbackHarnessHoldError(msg) from error
    return url


def _assert_before(state: DatabaseSnapshot) -> None:
    if state.revision != "20260727_0010":
        _hold("required_start_revision_0010")
    if state.manifold_present and (
        state.manifold_enabled or not state.manifold_pointers_null
    ):
        _hold("preactivation_manifold_not_inert")
    if re.fullmatch(r"[0-9a-f]{64}", state.dcinside_binding_sha256) is None:
        _hold("dcinside_prestate_invalid")


def _assert_peak(state: DatabaseSnapshot) -> None:
    if state.revision != "20260727_0011" or not state.manifold_present:
        _hold("upgrade_0011_not_observed")
    if state.manifold_enabled or not state.manifold_pointers_null:
        _hold("upgrade_not_prepared_inert")


def _assert_after(before: DatabaseSnapshot, after: DatabaseSnapshot) -> None:
    if after.revision != "20260727_0010":
        _hold("downgrade_0010_not_observed")
    if (
        not after.manifold_present
        or after.manifold_enabled
        or not after.manifold_pointers_null
    ):
        _hold("manifold_poststate_not_inert")
    if after.dcinside_binding_sha256 != before.dcinside_binding_sha256:
        _hold("dcinside_binding_changed")


def _write(path: Path, receipt: HarnessReceipt) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        receipt,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        _ = stream.write(f"{payload}\n")


def run_harness(
    options: Options,
    *,
    database: Database | None = None,
    external: ExternalRecorder | None = None,
) -> HarnessReceipt:
    """Run only guarded local migrations and record external argv as data."""
    url = _validate(options)
    if database is None:
        database = RealDatabase()
    if external is None:
        external = StubExternal()
    before = database.snapshot(url)
    _assert_before(before)
    with command_nine_environment(
        os.environ,
        options.json_out.parent,
        options.expected_sha,
        enabled=True,
    ):
        database.migrate(url, "upgrade", "20260727_0011")
    peak_error: RollbackHarnessHoldError | None = None
    peak = database.snapshot(url)
    try:
        _assert_peak(peak)
    except RollbackHarnessHoldError as error:
        peak_error = error
    finally:
        database.migrate(url, "downgrade", "20260727_0010")
    after = database.snapshot(url)
    _assert_after(before, after)
    if peak_error is not None:
        raise peak_error
    receipt: HarnessReceipt = {
        "accepted": True,
        "database": {
            "dcinside_binding_sha256": after.dcinside_binding_sha256,
            "dcinside_preserved": True,
            "manifold_enabled": False,
            "manifold_pointers_null": True,
            "name": EXPECTED_DATABASE,
            "revision_after": after.revision,
            "revision_before": before.revision,
            "revision_peak": peak.revision,
        },
        "external": external.record(render_matrix_b(options.expected_sha)),
        "mode": "disposable",
        "reviewed_sha": options.expected_sha,
        "schema": SCHEMA,
    }
    _write(options.json_out, receipt)
    return receipt


def main() -> int:
    """Execute the guarded drill and render only a redacted HOLD on failure."""
    try:
        _ = run_harness(parse_args())
    except (OSError, RollbackHarnessHoldError, ValueError) as error:
        _ = sys.stderr.write(f"HOLD:{error}\n")
        return 2
    return 0


__all__ = (
    "Database",
    "DatabaseSnapshot",
    "Options",
    "RollbackHarnessHoldError",
    "StubExternal",
    "main",
    "parse_args",
    "run_harness",
)

if __name__ == "__main__":
    raise SystemExit(main())
