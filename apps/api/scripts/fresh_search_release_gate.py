# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = []
# ///
# ─── How to run ───
# uv run --package monitor-api python apps/api/scripts/fresh_search_release_gate.py
"""Execute the fresh-search activation release gate."""

# ruff: noqa: E402

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve()
sys.path.insert(0, str(_SCRIPT.parents[3]))
sys.path.insert(0, str(_SCRIPT.parents[1]))

from app.services.release.source_activation_domain import (
    ActivationHoldError,
    ActivationState,
    ActivationTransition,
    CommitInput,
    CommitPlan,
    ReprepareInput,
    RepreparePlan,
    ReserveInput,
    ReservePlan,
)
from app.services.release.source_activation_plans import (
    plan_commit,
    plan_reprepare,
    plan_reserve,
    plan_restore,
    write_commit,
    write_reprepare,
    write_reserve,
    write_restore,
)

from scripts.release_gate_cli_runtime import main

__all__ = (
    "ActivationHoldError",
    "ActivationState",
    "ActivationTransition",
    "CommitInput",
    "CommitPlan",
    "ReprepareInput",
    "RepreparePlan",
    "ReserveInput",
    "ReservePlan",
    "plan_commit",
    "plan_reprepare",
    "plan_reserve",
    "plan_restore",
    "write_commit",
    "write_reprepare",
    "write_reserve",
    "write_restore",
)

if __name__ == "__main__":
    raise SystemExit(main())
