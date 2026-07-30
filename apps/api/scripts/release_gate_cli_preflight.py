"""Credential-free no-spend preflight adapter."""

# pyright: reportAny=false, reportArgumentType=false
# ruff: noqa: EM101, TC003

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import cast
from uuid import UUID

import yaml

from scripts.release_evidence_contracts import ReviewRecordAccess
from scripts.release_evidence_preflight import no_spend_preflight
from scripts.release_gate_cli_io import (
    JsonObject,
    read_document,
    strings,
    write_document,
)

_ROOT = Path(__file__).resolve().parents[3]


def _review(path: Path) -> JsonObject:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n") or "\n---\n" not in raw[4:]:
        raise ValueError("review_record_front_matter_missing")
    front_matter = raw[4:].split("\n---\n", 1)[0]
    parsed = cast("object", yaml.safe_load(front_matter))
    if not isinstance(parsed, dict):
        raise TypeError("review_record_front_matter_invalid")
    mapping = cast("dict[object, object]", parsed)
    if not all(isinstance(key, str) for key in mapping):
        raise ValueError("review_record_front_matter_invalid")
    return cast("JsonObject", mapping)


def _committed(path: Path) -> bool:
    relative = path.resolve().relative_to(_ROOT).as_posix()
    executable = shutil.which("git")
    if executable is None:
        raise ValueError("git_executable_missing")
    result = subprocess.run(  # noqa: S603
        (executable, "ls-files", "--error-unmatch", "--", relative),
        cwd=_ROOT,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _world_readable(path: Path) -> bool:
    if os.name == "nt":
        return False
    return bool(path.stat().st_mode & stat.S_IROTH)


def run_no_spend(args: argparse.Namespace) -> int:
    review_path = Path(args.review_record)
    review = _review(review_path)
    plan_name = review.get("plan_path")
    if not isinstance(plan_name, str):
        raise TypeError("review_record_plan_path_invalid")
    plan = (_ROOT / plan_name).resolve()
    _ = plan.relative_to(_ROOT)
    free_tier_path = Path(args.free_tier_result)
    evidence_join_path = (
        free_tier_path.parents[2] / "pre-0010" / "evidence-join.json"
    )
    receipt = no_spend_preflight(
        review_record=review,
        review_access=ReviewRecordAccess(
            committed=_committed(review_path),
            symlinked=review_path.is_symlink(),
            world_readable=_world_readable(review_path),
        ),
        live_plan_path=plan_name,
        live_plan_bytes=plan.read_bytes(),
        expected_sha=args.expected_sha,
        activation_nonce=UUID(args.activation_nonce),
        deployment_prestate=read_document(args.deployment_prestate),
        evidence_join_receipt=read_document(str(evidence_join_path)),
        provider_captures=tuple(
            read_document(path) for path in strings(args.provider_capture)
        ),
        production_measurements=read_document(args.production_measurements),
        free_tier_result=read_document(args.free_tier_result),
        predecessor_receipt=read_document(args.predecessor_receipt),
        bootstrap_attempt_exists=False,
    )
    write_document(args.json_out, cast("JsonObject", receipt))
    return 0


HANDLERS = {"no-spend-preflight": run_no_spend}

__all__ = ("HANDLERS",)
