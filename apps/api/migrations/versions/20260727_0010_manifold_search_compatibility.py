"""Add the Manifold enum boundary, indexed literal search, and release ledger."""

import base64
import hashlib
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Never

from alembic import op
from app.domain.types import JsonValue
from scripts.migration_dispatch_models import NoSpendReceipt, ReviewRoot
from sqlalchemy import text

revision: str = "20260727_0010"
down_revision: str | None = "20260726_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOWNGRADE_DEPENDENCY_CODE: Final = "manifold_downgrade_dependency"
MAX_BOOTSTRAP_RECEIPT_BYTES: Final = 8192
SEARCH_FOLD_FUNCTION_SQL: Final = "\n".join(
    (
        "CREATE FUNCTION search_fold_v1(input text)",
        "RETURNS text",
        "LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE",
        "AS $search_fold_v1$",
        "".join(  # noqa: FLY002 - avoids implicit concatenation under strict typing.
            (
                "SELECT translate(normalize(btrim(input, ",
                "chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(32)), NFC), ",
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') ",
                'COLLATE "C"',
            )
        ),
        "$search_fold_v1$",
    )
)
SEARCH_TEXT_COLUMN_SQL: Final = "\n".join(
    (
        "ALTER TABLE post_versions",
        "".join(  # noqa: FLY002 - avoids implicit concatenation under strict typing.
            (
                'ADD COLUMN search_text text COLLATE "C" GENERATED ALWAYS AS ',
                "(search_fold_v1(coalesce(title, '') || E'\\n' || ",
                "coalesce(body, ''))) STORED",
            )
        ),
    )
)
SEARCH_TEXT_INDEX_SQL: Final = (
    "CREATE INDEX ix_post_versions_search_text_trgm ON post_versions "
    'USING gin ((search_text COLLATE "C") gin_trgm_ops)'
)
_LEDGER_SQL: Final = """
CREATE TABLE release_roots (
    receipt_sha256 char(64) PRIMARY KEY
        CONSTRAINT release_root_receipt_sha CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
    canonical_receipt bytea NOT NULL,
    reviewed_sha char(40) NOT NULL,
    approved_plan_sha256 char(64) NOT NULL,
    approval_round_id char(64) NOT NULL,
    approval_launch_sha256s jsonb NOT NULL,
    activation_nonce uuid NOT NULL CONSTRAINT uq_release_root_activation UNIQUE,
    created_at_db timestamptz NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT uq_release_root_review_round
        UNIQUE (reviewed_sha, approved_plan_sha256, approval_round_id),
    CONSTRAINT uq_release_root_binding UNIQUE (
        receipt_sha256, reviewed_sha, approved_plan_sha256, activation_nonce
    )
);
CREATE TABLE release_no_spend_receipts (
    receipt_sha256 char(64) PRIMARY KEY
        CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
    canonical_receipt bytea NOT NULL,
    root_receipt_sha256 char(64) NOT NULL
        REFERENCES release_roots(receipt_sha256) ON DELETE RESTRICT,
    reviewed_sha char(40) NOT NULL,
    approved_plan_sha256 char(64) NOT NULL,
    activation_nonce uuid NOT NULL,
    predecessor_receipt_sha256 char(64) NOT NULL,
    accepted boolean NOT NULL,
    created_at_db timestamptz NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT uq_release_no_spend_root_activation
        UNIQUE (root_receipt_sha256, activation_nonce),
    CONSTRAINT release_no_spend_accepted CHECK (accepted),
    CONSTRAINT release_no_spend_predecessor_root
        CHECK (predecessor_receipt_sha256 = root_receipt_sha256),
    CONSTRAINT fk_release_no_spend_root_binding FOREIGN KEY (
        root_receipt_sha256, reviewed_sha, approved_plan_sha256, activation_nonce
    ) REFERENCES release_roots (
        receipt_sha256, reviewed_sha, approved_plan_sha256, activation_nonce
    ) ON DELETE RESTRICT,
    CONSTRAINT uq_release_no_spend_binding UNIQUE (
        receipt_sha256, reviewed_sha, approved_plan_sha256, activation_nonce
    )
);
CREATE TABLE release_operation_reservations (
    receipt_sha256 char(64) PRIMARY KEY
        CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
    canonical_receipt bytea NOT NULL,
    predecessor_receipt_sha256 char(64) NOT NULL,
    reviewed_sha char(40) NOT NULL,
    approved_plan_sha256 char(64) NOT NULL,
    activation_nonce uuid NOT NULL,
    dispatch_nonce uuid NOT NULL,
    workflow_file varchar(200) NOT NULL,
    display_title text NOT NULL,
    operation varchar(16) NOT NULL
        CONSTRAINT release_reservation_operation
        CHECK (operation IN ('upgrade', 'downgrade')),
    revision varchar(32) NOT NULL
        CONSTRAINT release_reservation_revision
        CHECK (revision IN ('20260727_0010', '20260727_0011')),
    attempt integer NOT NULL
        CONSTRAINT release_reservation_attempt CHECK (attempt IN (1, 2)),
    reserved_at_db timestamptz NOT NULL DEFAULT statement_timestamp(),
    selection_floor_at timestamptz NOT NULL,
    claimed_run_id bigint,
    claimed_at_db timestamptz,
    CONSTRAINT uq_release_reservation_dispatch
        UNIQUE (activation_nonce, dispatch_nonce),
    CONSTRAINT release_reservation_claim_pair
        CHECK ((claimed_run_id IS NULL) = (claimed_at_db IS NULL)),
    CONSTRAINT fk_release_reservation_predecessor FOREIGN KEY (
        predecessor_receipt_sha256,
        reviewed_sha,
        approved_plan_sha256,
        activation_nonce
    ) REFERENCES release_no_spend_receipts (
        receipt_sha256, reviewed_sha, approved_plan_sha256, activation_nonce
    ) ON DELETE RESTRICT,
    CONSTRAINT uq_release_reservation_binding UNIQUE (
        receipt_sha256,
        reviewed_sha,
        approved_plan_sha256,
        activation_nonce,
        dispatch_nonce,
        operation,
        revision,
        attempt
    )
);
CREATE UNIQUE INDEX uq_release_reservation_claimed_run
ON release_operation_reservations (claimed_run_id) NULLS NOT DISTINCT;
CREATE TABLE release_operation_receipts (
    receipt_sha256 char(64) PRIMARY KEY
        CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
    canonical_receipt bytea NOT NULL,
    reservation_receipt_sha256 char(64) NOT NULL
        REFERENCES release_operation_reservations(receipt_sha256) ON DELETE RESTRICT,
    predecessor_receipt_sha256 char(64) NOT NULL,
    reviewed_sha char(40) NOT NULL,
    approved_plan_sha256 char(64) NOT NULL,
    activation_nonce uuid NOT NULL,
    dispatch_nonce uuid NOT NULL,
    operation varchar(16) NOT NULL CHECK (operation IN ('upgrade', 'downgrade')),
    revision varchar(32) NOT NULL
        CHECK (revision IN ('20260727_0010', '20260727_0011')),
    attempt integer NOT NULL CHECK (attempt IN (1, 2)),
    run_id bigint NOT NULL,
    head_sha char(40) NOT NULL,
    artifact_sha256 char(64) NOT NULL,
    accepted boolean NOT NULL,
    terminal_for_attempt boolean NOT NULL,
    retry_permitted boolean NOT NULL,
    state_before varchar(32) NOT NULL,
    state_after varchar(32) NOT NULL,
    enum_residue boolean NOT NULL DEFAULT false,
    committed_revision varchar(32) NOT NULL,
    created_at_db timestamptz NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT uq_release_operation_identity UNIQUE (
        activation_nonce, dispatch_nonce, operation, revision, attempt, run_id
    ),
    CONSTRAINT release_operation_predecessor_reservation
        CHECK (predecessor_receipt_sha256 = reservation_receipt_sha256),
    CONSTRAINT release_operation_retry_terminal_failure
        CHECK (NOT retry_permitted OR (terminal_for_attempt AND NOT accepted)),
    CONSTRAINT release_operation_accept_terminal
        CHECK (NOT accepted OR (terminal_for_attempt AND NOT retry_permitted)),
    CONSTRAINT fk_release_operation_reservation_binding FOREIGN KEY (
        reservation_receipt_sha256,
        reviewed_sha,
        approved_plan_sha256,
        activation_nonce,
        dispatch_nonce,
        operation,
        revision,
        attempt
    ) REFERENCES release_operation_reservations (
        receipt_sha256,
        reviewed_sha,
        approved_plan_sha256,
        activation_nonce,
        dispatch_nonce,
        operation,
        revision,
        attempt
    ) ON DELETE RESTRICT
)
"""
_DOWNGRADE_GUARD_SQL: Final = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM community_sources WHERE platform = 'manifold'
    ) OR EXISTS (
        SELECT 1
        FROM release_operation_reservations
        WHERE operation <> 'upgrade' OR revision <> '20260727_0010'
    ) OR EXISTS (
        SELECT 1
        FROM release_operation_receipts
        WHERE operation <> 'upgrade' OR revision <> '20260727_0010'
    ) THEN
        RAISE EXCEPTION 'manifold_downgrade_dependency'
            USING ERRCODE = '55000';
    END IF;
END
$$
"""


class BootstrapReceiptError(RuntimeError):
    """Stable bootstrap refusal that never includes transported receipt bytes."""


def _reject_bootstrap(code: str) -> Never:
    raise BootstrapReceiptError(code)


def _canonical_bytes(value: JsonValue) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _bootstrap_release_ledger() -> None:
    root_b64 = os.environ.get("MIGRATION_REVIEW_ROOT_B64", "")
    no_spend_b64 = os.environ.get("MIGRATION_NO_SPEND_RECEIPT_B64", "")
    if not root_b64 and not no_spend_b64:
        return
    if not root_b64 or not no_spend_b64:
        _reject_bootstrap("bootstrap_receipt_pair_required")
    root_bytes = base64.b64decode(root_b64, validate=True)
    no_spend_bytes = base64.b64decode(no_spend_b64, validate=True)
    if (
        len(root_bytes) > MAX_BOOTSTRAP_RECEIPT_BYTES
        or len(no_spend_bytes) > MAX_BOOTSTRAP_RECEIPT_BYTES
    ):
        _reject_bootstrap("bootstrap_receipt_oversize")
    root = ReviewRoot.model_validate_json(root_bytes)
    no_spend = NoSpendReceipt.model_validate_json(no_spend_bytes)
    if root_bytes != _canonical_bytes(root.model_dump(mode="json")):
        _reject_bootstrap("bootstrap_root_noncanonical")
    if no_spend_bytes != _canonical_bytes(no_spend.model_dump(mode="json")):
        _reject_bootstrap("bootstrap_no_spend_noncanonical")
    expected_sha = os.environ["MIGRATION_EXPECTED_COMMIT_SHA"]
    expected_plan = os.environ["MIGRATION_EXPECTED_PLAN_SHA256"]
    activation_nonce = os.environ["MIGRATION_ACTIVATION_NONCE"]
    dispatch_nonce = os.environ["MIGRATION_DISPATCH_NONCE"]
    attempt = int(os.environ["MIGRATION_ATTEMPT"])
    run_id = int(os.environ["GITHUB_RUN_ID"])
    sha256_values = (
        expected_plan,
        root.approval_round_id,
        *root.approval_launch_sha256s,
        *root.protected_identity_hashes.model_dump().values(),
        no_spend.predecessor_receipt_sha256,
    )
    if (
        re.fullmatch(r"[0-9a-f]{40}", expected_sha) is None
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(value)) is None
            for value in sha256_values
        )
        or attempt not in {1, 2}
        or run_id <= 0
    ):
        _reject_bootstrap("bootstrap_identity_invalid")
    if (
        root.reviewed_sha != expected_sha
        or root.approved_plan_sha256 != expected_plan
        or str(root.activation_nonce) != activation_nonce
        or no_spend.reviewed_sha != expected_sha
        or no_spend.approved_plan_sha256 != expected_plan
        or str(no_spend.activation_nonce) != activation_nonce
    ):
        _reject_bootstrap("bootstrap_receipt_binding_mismatch")
    root_sha = hashlib.sha256(root_bytes).hexdigest()
    no_spend_sha = hashlib.sha256(no_spend_bytes).hexdigest()
    if no_spend.predecessor_receipt_sha256 != root_sha:
        _reject_bootstrap("bootstrap_predecessor_mismatch")
    backup_path = Path(os.environ["RUNNER_TEMP"]) / "pre-migration.dump.age"
    artifact_sha = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    display_title = (
        f"migrate-upgrade-20260727_0010-{dispatch_nonce}-attempt-{attempt}"
    )
    reservation: JsonValue = {
        "activation_nonce": activation_nonce,
        "attempt": attempt,
        "command": "migration-reservation",
        "dispatch_nonce": dispatch_nonce,
        "display_title": display_title,
        "operation": "upgrade",
        "predecessor_receipt_sha256": no_spend_sha,
        "revision": revision,
        "reviewed_sha": expected_sha,
        "approved_plan_sha256": expected_plan,
        "schema_version": 1,
        "workflow_file": ".github/workflows/migrate.yml",
    }
    reservation_bytes = _canonical_bytes(reservation)
    reservation_sha = hashlib.sha256(reservation_bytes).hexdigest()
    operation_receipt: JsonValue = {
        "accepted": True,
        "activation_nonce": activation_nonce,
        "approved_plan_sha256": expected_plan,
        "artifact_sha256": artifact_sha,
        "attempt": attempt,
        "command": "migrate-0010-bootstrap",
        "committed_revision": revision,
        "dispatch_nonce": dispatch_nonce,
        "enum_residue": False,
        "head_sha": expected_sha,
        "operation": "upgrade",
        "predecessor_receipt_sha256": reservation_sha,
        "reservation_receipt_sha256": reservation_sha,
        "retry_permitted": False,
        "reviewed_sha": expected_sha,
        "revision": revision,
        "run_id": run_id,
        "schema_version": 1,
        "state_after": revision,
        "state_before": down_revision,
        "terminal_for_attempt": True,
    }
    operation_bytes = _canonical_bytes(operation_receipt)
    operation_sha = hashlib.sha256(operation_bytes).hexdigest()
    bind = op.get_bind()
    parameters = {
        "activation_nonce": activation_nonce,
        "artifact_sha": artifact_sha,
        "attempt": attempt,
        "dispatch_nonce": dispatch_nonce,
        "display_title": display_title,
        "launches": json.dumps(root.approval_launch_sha256s),
        "no_spend_bytes": no_spend_bytes,
        "no_spend_sha": no_spend_sha,
        "operation_bytes": operation_bytes,
        "operation_sha": operation_sha,
        "plan_sha": expected_plan,
        "reservation_bytes": reservation_bytes,
        "reservation_sha": reservation_sha,
        "reviewed_sha": expected_sha,
        "root_bytes": root_bytes,
        "root_sha": root_sha,
        "round_id": root.approval_round_id,
        "run_id": run_id,
    }
    statements = (
        """
            INSERT INTO release_roots VALUES (
                :root_sha, :root_bytes, :reviewed_sha, :plan_sha, :round_id,
                CAST(:launches AS jsonb), CAST(:activation_nonce AS uuid),
                statement_timestamp()
            )
        """,
        """
            INSERT INTO release_no_spend_receipts VALUES (
                :no_spend_sha, :no_spend_bytes, :root_sha, :reviewed_sha,
                :plan_sha, CAST(:activation_nonce AS uuid), :root_sha, true,
                statement_timestamp()
            )
        """,
        """
            INSERT INTO release_operation_reservations VALUES (
                :reservation_sha, :reservation_bytes, :no_spend_sha,
                :reviewed_sha, :plan_sha, CAST(:activation_nonce AS uuid),
                CAST(:dispatch_nonce AS uuid), '.github/workflows/migrate.yml',
                :display_title, 'upgrade', '20260727_0010', :attempt,
                statement_timestamp(), statement_timestamp(), :run_id,
                statement_timestamp()
            )
        """,
        """
            INSERT INTO release_operation_receipts VALUES (
                :operation_sha, :operation_bytes, :reservation_sha,
                :reservation_sha, :reviewed_sha, :plan_sha,
                CAST(:activation_nonce AS uuid), CAST(:dispatch_nonce AS uuid),
                'upgrade', '20260727_0010', :attempt, :run_id, :reviewed_sha,
                :artifact_sha, true, true, false, '20260726_0009',
                '20260727_0010', false, '20260727_0010',
                statement_timestamp()
            )
        """,
    )
    for statement in statements:
        _ = bind.execute(text(statement), parameters)


def upgrade() -> None:
    """Create the committed enum boundary before reversible compatibility DDL."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE source_platform ADD VALUE IF NOT EXISTS 'manifold'")
    op.execute(
        "ALTER TYPE terminal_reason ADD VALUE IF NOT EXISTS 'reviewed_byte_cap'"
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(SEARCH_FOLD_FUNCTION_SQL)
    op.execute(SEARCH_TEXT_COLUMN_SQL)
    op.execute(SEARCH_TEXT_INDEX_SQL)
    for statement in _LEDGER_SQL.split(";\n"):
        if statement.strip():
            op.execute(statement)
    for table_name in (
        "release_roots",
        "release_no_spend_receipts",
        "release_operation_reservations",
        "release_operation_receipts",
    ):
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(
            "".join(
                (
                    f"REVOKE ALL PRIVILEGES ON TABLE public.{table_name} FROM ",
                    "PUBLIC, anon, authenticated",
                )
            )
        )
    _bootstrap_release_ledger()


def downgrade() -> None:
    """Refuse dependent rollback before dropping reversible 0010 objects."""
    op.execute(_DOWNGRADE_GUARD_SQL)
    op.execute("DROP TABLE release_operation_receipts")
    op.execute("DROP TABLE release_operation_reservations")
    op.execute("DROP TABLE release_no_spend_receipts")
    op.execute("DROP TABLE release_roots")
    op.execute("DROP INDEX ix_post_versions_search_text_trgm")
    op.execute("ALTER TABLE post_versions DROP COLUMN search_text")
    op.execute("DROP FUNCTION search_fold_v1(text)")
