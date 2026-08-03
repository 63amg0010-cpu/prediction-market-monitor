"""Rebind the release root after canonicalizing activation evidence."""

# ruff: noqa: EM101, TC003, TRY004
# pyright: reportAny=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false, reportUnusedCallResult=false

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence

from alembic import context, op
from scripts.migration_dispatch_core import parse_body
from scripts.migration_dispatch_models import NoSpendReceipt, ReviewRoot
from sqlalchemy import text

revision: str = "20260803_0010g"
down_revision: str | None = "20260803_0010f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
CORRECTION_COMMAND = "release-correction-0010g"
STATE_BEFORE = "20260803_0010f"
ENV_PREFIX = "MIGRATION_CANONICAL_REBIND"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError("activation_reservation_rebind_input_missing")
    return value


def _input(suffix: str) -> str:
    return _environment(f"{ENV_PREFIX}_{suffix}")


def _load_inputs() -> tuple[ReviewRoot, NoSpendReceipt, bytes, bytes]:
    root_hash = _input("REVIEW_ROOT_SHA256")
    no_spend_hash = _input("NO_SPEND_RECEIPT_SHA256")
    root = parse_body(
        _input("REVIEW_ROOT_B64"),
        root_hash,
        ReviewRoot,
    )
    no_spend = parse_body(
        _input("NO_SPEND_RECEIPT_B64"),
        no_spend_hash,
        NoSpendReceipt,
    )
    if (
        root.reviewed_sha
        != _input("EXPECTED_COMMIT_SHA")
        or root.approved_plan_sha256
        != _input("EXPECTED_PLAN_SHA256")
        or str(root.activation_nonce)
        != _input("ACTIVATION_NONCE")
        or no_spend.reviewed_sha != root.reviewed_sha
        or no_spend.approved_plan_sha256 != root.approved_plan_sha256
        or no_spend.activation_nonce != root.activation_nonce
        or no_spend.predecessor_receipt_sha256 != root_hash
    ):
        raise RuntimeError("activation_reservation_rebind_binding_mismatch")
    return (
        root,
        no_spend,
        _canonical(root.model_dump(mode="json")),
        _canonical(no_spend.model_dump(mode="json")),
    )


def upgrade() -> None:
    """Append a reviewed root without activating or changing a source."""
    if context.is_offline_mode():
        op.execute("SELECT 1")
        return
    root, _no_spend, root_bytes, no_spend_bytes = _load_inputs()
    bind = op.get_bind()
    if not bind.execute(
        text("SELECT to_regclass('public.release_receipt_chain')")
    ).scalar_one():
        raise RuntimeError("activation_reservation_rebind_foundation_missing")
    prior_receipt = (
        bind.execute(
            text(
                """
                SELECT convert_from(canonical_receipt, 'UTF8')::jsonb AS receipt
                FROM release_roots
                ORDER BY created_at_db DESC
                LIMIT 1
                """
            )
        )
        .mappings()
        .one()["receipt"]
    )
    if not isinstance(prior_receipt, dict):
        raise RuntimeError("activation_reservation_rebind_prior_root_invalid")
    if (
        tuple(prior_receipt.get("public_provider_names", ()))
        != root.public_provider_names
        or prior_receipt.get("protected_identity_hashes")
        != root.protected_identity_hashes.model_dump(mode="json")
        or prior_receipt.get("approved_plan_sha256") != root.approved_plan_sha256
    ):
        raise RuntimeError("activation_reservation_rebind_plan_or_identity_drift")

    root_sha = hashlib.sha256(root_bytes).hexdigest()
    no_spend_sha = hashlib.sha256(no_spend_bytes).hexdigest()
    db_now = bind.execute(text("SELECT transaction_timestamp()")).scalar_one()
    parameters = {
        "activation_nonce": root.activation_nonce,
        "approval_launches": json.dumps(list(root.approval_launch_sha256s)),
        "approval_round_id": root.approval_round_id,
        "created_at_db": db_now,
        "no_spend": no_spend_bytes,
        "no_spend_sha": no_spend_sha,
        "plan_sha": root.approved_plan_sha256,
        "reviewed_sha": root.reviewed_sha,
        "root": root_bytes,
        "root_sha": root_sha,
    }
    bind.execute(
        text(
            """
            INSERT INTO release_roots (
                receipt_sha256, canonical_receipt, reviewed_sha,
                approved_plan_sha256, approval_round_id,
                approval_launch_sha256s, activation_nonce, created_at_db
            ) VALUES (
                :root_sha, :root, :reviewed_sha, :plan_sha,
                :approval_round_id, CAST(:approval_launches AS jsonb),
                :activation_nonce, :created_at_db
            )
            """
        ),
        parameters,
    )
    bind.execute(
        text(
            """
            INSERT INTO release_no_spend_receipts (
                receipt_sha256, canonical_receipt, root_receipt_sha256,
                reviewed_sha, approved_plan_sha256, activation_nonce,
                predecessor_receipt_sha256, accepted, created_at_db
            ) VALUES (
                :no_spend_sha, :no_spend, :root_sha, :reviewed_sha,
                :plan_sha, :activation_nonce, :root_sha, true,
                :created_at_db
            )
            """
        ),
        parameters,
    )
    bind.execute(
        text(
            """
            INSERT INTO release_receipt_chain (
                receipt_sha256, canonical_receipt, command, reviewed_sha,
                approved_plan_sha256, approval_round_id,
                approval_launch_sha256s, activation_nonce, dispatch_nonce,
                attempt, accepted, terminal_for_attempt, retry_permitted,
                predecessor_receipt_sha256, created_at_db
            ) VALUES (
                :root_sha, :root, 'review-root', :reviewed_sha,
                :plan_sha, :approval_round_id,
                CAST(:approval_launches AS jsonb), :activation_nonce, NULL,
                0, true, true, false, NULL, :created_at_db
            ), (
                :no_spend_sha, :no_spend, 'no-spend-preflight',
                :reviewed_sha, :plan_sha, :approval_round_id,
                CAST(:approval_launches AS jsonb), :activation_nonce, NULL,
                0, true, true, false, :root_sha, :created_at_db
            )
            """
        ),
        parameters,
    )
    receipt_body = {
        "schema_version": 1,
        "command": CORRECTION_COMMAND,
        "reviewed_sha": root.reviewed_sha,
        "approved_plan_sha256": root.approved_plan_sha256,
        "approval_round_id": root.approval_round_id,
        "approval_launch_sha256s": list(root.approval_launch_sha256s),
        "activation_nonce": str(root.activation_nonce),
        "dispatch_nonce": _input("DISPATCH_NONCE"),
        "attempt": int(_input("ATTEMPT")),
        "state_before": STATE_BEFORE,
        "state_after": revision,
        "accepted": True,
        "terminal_for_attempt": True,
        "retry_permitted": False,
        "predecessor_receipt_sha256": no_spend_sha,
    }
    receipt_bytes = _canonical(receipt_body)
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    bind.execute(
        text(
            """
            INSERT INTO release_receipt_chain (
                receipt_sha256, canonical_receipt, command, reviewed_sha,
                approved_plan_sha256, approval_round_id,
                approval_launch_sha256s, activation_nonce, dispatch_nonce,
                attempt, accepted, terminal_for_attempt, retry_permitted,
                predecessor_receipt_sha256, created_at_db
            ) VALUES (
                :receipt_sha, :receipt, :command,
                :reviewed_sha, :plan_sha, :approval_round_id,
                CAST(:approval_launches AS jsonb), :activation_nonce,
                CAST(:dispatch_nonce AS uuid), :attempt, true, true, false,
                :no_spend_sha, :created_at_db
            )
            """
        ),
        {
            **parameters,
            "command": CORRECTION_COMMAND,
            "attempt": int(_input("ATTEMPT")),
            "dispatch_nonce": _input("DISPATCH_NONCE"),
            "receipt": receipt_bytes,
            "receipt_sha": receipt_sha,
        },
    )


def downgrade() -> None:
    """Remove only an unused 0010g root rebind."""
    if context.is_offline_mode():
        op.execute("SELECT 1")
        return
    bind = op.get_bind()
    correction_nonce = bind.execute(
        text(
            """
            SELECT activation_nonce
            FROM release_receipt_chain
            WHERE command = :command
            """
        ),
        {"command": CORRECTION_COMMAND},
    ).scalar_one_or_none()
    if correction_nonce is None:
        return
    unexpected = bind.execute(
        text(
            """
            SELECT count(*)
            FROM release_receipt_chain
            WHERE activation_nonce = :nonce
              AND command NOT IN ('review-root', 'no-spend-preflight')
              AND command <> :command
            """
        ),
        {"command": CORRECTION_COMMAND, "nonce": correction_nonce},
    ).scalar_one()
    if unexpected:
        raise RuntimeError("activation_reservation_rebind_downgrade_dependency")
    bind.execute(
        text("DELETE FROM release_receipt_chain WHERE activation_nonce = :nonce"),
        {"nonce": correction_nonce},
    )
    bind.execute(
        text("DELETE FROM release_no_spend_receipts WHERE activation_nonce=:nonce"),
        {"nonce": correction_nonce},
    )
    bind.execute(
        text("DELETE FROM release_roots WHERE activation_nonce=:nonce"),
        {"nonce": correction_nonce},
    )
