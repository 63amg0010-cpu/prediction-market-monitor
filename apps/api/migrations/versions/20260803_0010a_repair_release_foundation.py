"""Repair the pre-0011 release foundation without enabling Manifold."""

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
from scripts.release_foundation_schema import DOWNGRADE_SQL, UPGRADE_SQL
from sqlalchemy import text

revision: str = "20260803_0010a"
down_revision: str | None = "20260727_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
        raise RuntimeError("release_correction_input_missing")
    return value


def _load_inputs() -> tuple[ReviewRoot, NoSpendReceipt, bytes, bytes]:
    root_hash = _environment("MIGRATION_CORRECTION_REVIEW_ROOT_SHA256")
    no_spend_hash = _environment("MIGRATION_CORRECTION_NO_SPEND_RECEIPT_SHA256")
    root = parse_body(
        _environment("MIGRATION_CORRECTION_REVIEW_ROOT_B64"),
        root_hash,
        ReviewRoot,
    )
    no_spend = parse_body(
        _environment("MIGRATION_CORRECTION_NO_SPEND_RECEIPT_B64"),
        no_spend_hash,
        NoSpendReceipt,
    )
    if (
        root.reviewed_sha != _environment("MIGRATION_CORRECTION_EXPECTED_COMMIT_SHA")
        or root.approved_plan_sha256
        != _environment("MIGRATION_CORRECTION_EXPECTED_PLAN_SHA256")
        or str(root.activation_nonce)
        != _environment("MIGRATION_CORRECTION_ACTIVATION_NONCE")
        or no_spend.reviewed_sha != root.reviewed_sha
        or no_spend.approved_plan_sha256 != root.approved_plan_sha256
        or no_spend.activation_nonce != root.activation_nonce
        or no_spend.predecessor_receipt_sha256 != root_hash
    ):
        raise RuntimeError("release_correction_binding_mismatch")
    return (
        root,
        no_spend,
        _canonical(root.model_dump(mode="json")),
        _canonical(no_spend.model_dump(mode="json")),
    )


def _execute_statements(sql: str) -> None:
    for statement in sql.split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    """Append a corrected root and install the generic receipt foundation."""
    if context.is_offline_mode():
        _execute_statements(UPGRADE_SQL)
        return
    root, _no_spend, root_bytes, no_spend_bytes = _load_inputs()
    bind = op.get_bind()
    release_chain = bind.execute(
        text("SELECT to_regclass('public.release_receipt_chain')")
    ).scalar()
    if release_chain:
        raise RuntimeError("release_correction_already_applied")
    prior = (
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
        .one()
    )
    prior_receipt = prior["receipt"]
    if not isinstance(prior_receipt, dict):
        raise RuntimeError("release_correction_prior_root_invalid")
    if tuple(
        prior_receipt.get("public_provider_names", ())
    ) != root.public_provider_names or prior_receipt.get(
        "protected_identity_hashes"
    ) != root.protected_identity_hashes.model_dump(mode="json"):
        raise RuntimeError("release_correction_identity_drift")
    root_sha = hashlib.sha256(root_bytes).hexdigest()
    no_spend_sha = hashlib.sha256(no_spend_bytes).hexdigest()
    parameters = {
        "activation_nonce": root.activation_nonce,
        "approval_launches": json.dumps(list(root.approval_launch_sha256s)),
        "approval_round_id": root.approval_round_id,
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
                approval_launch_sha256s, activation_nonce
            ) VALUES (
                :root_sha, :root, :reviewed_sha, :plan_sha,
                :approval_round_id, CAST(:approval_launches AS jsonb),
                :activation_nonce
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
                predecessor_receipt_sha256, accepted
            ) VALUES (
                :no_spend_sha, :no_spend, :root_sha, :reviewed_sha,
                :plan_sha, :activation_nonce, :root_sha, true
            )
            """
        ),
        parameters,
    )
    _execute_statements(UPGRADE_SQL)
    db_now = bind.execute(text("SELECT transaction_timestamp()")).scalar_one()
    receipt_body = {
        "schema_version": 1,
        "command": "release-correction-0010a",
        "reviewed_sha": root.reviewed_sha,
        "approved_plan_sha256": root.approved_plan_sha256,
        "approval_round_id": root.approval_round_id,
        "approval_launch_sha256s": list(root.approval_launch_sha256s),
        "activation_nonce": str(root.activation_nonce),
        "dispatch_nonce": _environment("MIGRATION_CORRECTION_DISPATCH_NONCE"),
        "attempt": int(_environment("MIGRATION_CORRECTION_ATTEMPT")),
        "state_before": "20260727_0010",
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
                :receipt_sha, :receipt, 'release-correction-0010a',
                :reviewed_sha, :plan_sha, :approval_round_id,
                CAST(:approval_launches AS jsonb), :activation_nonce,
                CAST(:dispatch_nonce AS uuid), :attempt, true, true, false,
                :no_spend_sha, :created_at_db
            )
            """
        ),
        {
            **parameters,
            "attempt": int(_environment("MIGRATION_CORRECTION_ATTEMPT")),
            "created_at_db": db_now,
            "dispatch_nonce": _environment(
                "MIGRATION_CORRECTION_DISPATCH_NONCE"
            ),
            "receipt": receipt_bytes,
            "receipt_sha": receipt_sha,
        },
    )


def downgrade() -> None:
    """Remove only an unused correction foundation and retain the old 0010 root."""
    if context.is_offline_mode():
        _execute_statements(DOWNGRADE_SQL)
        return
    bind = op.get_bind()
    row = bind.execute(
        text(
            """
            SELECT activation_nonce
            FROM release_receipt_chain
            WHERE command = 'release-correction-0010a'
            """
        )
    ).one()
    correction_nonce = row[0]
    unexpected = bind.execute(
        text(
            """
            SELECT count(*)
            FROM release_receipt_chain
            WHERE command NOT IN (
                'review-root', 'no-spend-preflight', 'migration-reservation',
                'migrate-0010-bootstrap', 'release-correction-0010a'
            )
            """
        )
    ).scalar_one()
    if unexpected:
        raise RuntimeError("release_correction_downgrade_dependency")
    _execute_statements(DOWNGRADE_SQL)
    bind.execute(
        text("DELETE FROM release_no_spend_receipts WHERE activation_nonce=:nonce"),
        {"nonce": correction_nonce},
    )
    bind.execute(
        text("DELETE FROM release_roots WHERE activation_nonce=:nonce"),
        {"nonce": correction_nonce},
    )
