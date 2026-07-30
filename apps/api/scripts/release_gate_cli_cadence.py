"""Durable PostgreSQL cadence command adapter."""

# pyright: reportAny=false, reportArgumentType=false
# ruff: noqa: EM101, PLR2004, TC003

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import anyio

from scripts.release_cadence import materialize_epoch
from scripts.release_chain_common import (
    bindings_of,
    build_receipt,
    require_bindings,
    verified_receipt,
    write_receipt,
)
from scripts.release_gate_cli_io import JsonObject, read_document, strings
from scripts.release_runtime_database import CadencePostgresRuntime
from scripts.release_runtime_io import BoundedPathReceiptIO

_IO = BoundedPathReceiptIO()


def _find(document: object, name: str) -> str:
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            typed = cast("dict[object, object]", value)
            candidate = typed.get(name)
            if isinstance(candidate, str):
                found.add(candidate)
            for child in typed.values():
                walk(child)
        elif isinstance(value, list):
            for child in cast("list[object]", value):
                walk(child)

    walk(document)
    if len(found) != 1:
        message = f"cadence_{name}_missing_or_ambiguous"
        raise ValueError(message)
    return next(iter(found))


def _sources(args: argparse.Namespace) -> tuple[UUID, UUID]:
    values = tuple(UUID(value) for value in strings(args.source_id))
    if len(values) != 2 or len(set(values)) != 2:
        raise ValueError("cadence_source_set_invalid")
    return values


def _bindings(args: argparse.Namespace, predecessor: Mapping[str, object]) -> None:
    expected = bindings_of(cast("JsonObject", predecessor))
    if (
        expected.reviewed_sha != args.expected_sha
        or expected.approved_plan_sha256 != args.expected_plan_sha256
        or expected.activation_nonce != args.activation_nonce
    ):
        raise ValueError("cadence_binding_mismatch")


def run_cadence(args: argparse.Namespace) -> int:
    runtime = CadencePostgresRuntime.from_env(args.database_url_env)
    try:
        return anyio.run(_execute, args, runtime)
    finally:
        anyio.run(runtime.dispose)


async def _execute(
    args: argparse.Namespace,
    runtime: CadencePostgresRuntime,
) -> int:
    predecessor = verified_receipt(_IO, Path(args.predecessor_receipt))
    _bindings(args, predecessor)
    epoch_id = UUID(args.epoch_id)
    source_ids = _sources(args)
    if args.phase == "initial":
        if args.activation_chain is None:
            raise ValueError("cadence_activation_chain_required")
        activation_path = Path(args.activation_chain)
        activation = verified_receipt(_IO, activation_path)
        require_bindings(activation, bindings_of(predecessor))
        if activation["receipt_sha256"] != predecessor["receipt_sha256"]:
            raise ValueError("cadence_activation_predecessor_mismatch")
        raw = read_document(str(activation_path))
        anchor = _find(raw, "cadence_anchor_at")
        binding = _find(raw, "binding_sha256")
        scope = _find(raw, "scope_sha256")
        epoch, slots = materialize_epoch(
            epoch_id,
            datetime.fromisoformat(anchor),
            source_ids,
            binding,
            scope,
        )
        await runtime.store.materialize(epoch, slots)
        snapshot = await runtime.snapshot(epoch_id)
        observed = snapshot.observed_at
        accepted_collection = snapshot.accepted_collection_slots
        accepted_verifier = snapshot.accepted_verifier_slots
        status = "OPERATIONAL_PENDING_CADENCE"
        reason = "day_zero_never_complete"
    else:
        snapshot = await runtime.snapshot(epoch_id)
        if snapshot.epoch.expected_source_ids != tuple(sorted(source_ids, key=str)):
            raise ValueError("cadence_source_set_mismatch")
        observed = snapshot.observed_at
        accepted_collection = snapshot.accepted_collection_slots
        accepted_verifier = snapshot.accepted_verifier_slots
        complete = (
            args.phase == "acceptance"
            and observed >= snapshot.epoch.closes_at
            and snapshot.epoch.invalidated_at is None
            and accepted_collection == 240
            and accepted_verifier == 2880
        )
        status = "COMPLETE" if complete else (
            "OPERATIONAL_PENDING_CADENCE"
            if args.phase == "status"
            else "HOLD"
        )
        reason = (
            "complete" if complete else
            "day_zero_never_complete" if args.phase == "status" else
            "missing_accepted_slots"
        )
        if args.phase == "acceptance":
            if args.prior_cadence is None:
                raise ValueError("cadence_prior_status_required")
            prior = verified_receipt(_IO, Path(args.prior_cadence))
            require_bindings(prior, bindings_of(predecessor))
            details = prior.get("details")
            if (
                prior.get("command") != "cadence-status"
                or not isinstance(details, dict)
                or details.get("phase") != "status"
                or details.get("epoch_id") != str(epoch_id)
                or details.get("source_ids")
                != [str(value) for value in source_ids]
            ):
                raise ValueError("cadence_prior_status_mismatch")
    receipt = build_receipt(
        command=f"cadence-{args.phase}",
        predecessor=predecessor,
        clock=lambda: observed,
        details={
            "phase": args.phase,
            "epoch_id": str(epoch_id),
            "source_ids": [str(value) for value in source_ids],
            "accepted_collection_slots": accepted_collection,
            "accepted_verifier_slots": accepted_verifier,
            "status": status,
            "reason": reason,
            "cadence_30d": "PASS" if status == "COMPLETE" else "HOLD",
        },
    )
    write_receipt(_IO, Path(args.json_out), receipt)
    return 0


HANDLERS = {"cadence": run_cadence}

__all__ = ("HANDLERS",)
