"""Retained 0011 PostgreSQL schema for workflow-based cadence evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from sqlalchemy import TextClause, text

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Connection

SCHEMA_SQL: Final = """
CREATE TABLE IF NOT EXISTS cadence_epoch_contracts (
    cadence_epoch_id uuid PRIMARY KEY
        REFERENCES source_cadence_epochs(id) ON DELETE RESTRICT,
    epoch_sha256 char(64) NOT NULL UNIQUE
        CHECK (epoch_sha256 ~ '^[0-9a-f]{64}$'),
    dcinside_source_id uuid NOT NULL
        REFERENCES community_sources(id) ON DELETE RESTRICT,
    manifold_source_id uuid NOT NULL
        REFERENCES community_sources(id) ON DELETE RESTRICT,
    binding_sha256 char(64) NOT NULL
        CHECK (binding_sha256 ~ '^[0-9a-f]{64}$'),
    scope_sha256 char(64) NOT NULL
        CHECK (scope_sha256 ~ '^[0-9a-f]{64}$'),
    window_closes_at timestamptz NOT NULL,
    invalidated_at timestamptz,
    created_at_db timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT cadence_expected_sources_distinct
        CHECK (dcinside_source_id <> manifold_source_id),
    CONSTRAINT cadence_invalidation_after_creation
        CHECK (invalidated_at IS NULL OR invalidated_at >= created_at_db)
);
CREATE TABLE IF NOT EXISTS cadence_workflow_slots (
    cadence_epoch_id uuid NOT NULL
        REFERENCES cadence_epoch_contracts(cadence_epoch_id) ON DELETE RESTRICT,
    schedule_kind varchar(16) NOT NULL
        CHECK (schedule_kind IN ('collection', 'verifier')),
    slot_key char(20) NOT NULL
        CHECK (slot_key ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:00Z$'),
    due_at timestamptz NOT NULL,
    accepted_attempt_id uuid,
    created_at_db timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (cadence_epoch_id, schedule_kind, slot_key),
    CONSTRAINT cadence_collection_slot_key CHECK (
        schedule_kind <> 'collection'
        OR slot_key ~ 'T(00|03|06|09|12|15|18|21):17:00Z$'
    ),
    CONSTRAINT cadence_verifier_slot_key CHECK (
        schedule_kind <> 'verifier'
        OR slot_key ~ 'T[0-9]{2}:(00|15|30|45):00Z$'
    )
);
CREATE TABLE IF NOT EXISTS cadence_workflow_attempts (
    attempt_id uuid PRIMARY KEY,
    cadence_epoch_id uuid NOT NULL,
    schedule_kind varchar(16) NOT NULL,
    slot_key char(20) NOT NULL,
    workflow_mode varchar(32) NOT NULL,
    workflow_file varchar(64) NOT NULL
        CHECK (workflow_file IN ('collect.yml', 'verify.yml')),
    workflow_run_id bigint NOT NULL CHECK (workflow_run_id > 0),
    workflow_run_attempt integer NOT NULL CHECK (workflow_run_attempt > 0),
    cadence_attempt integer NOT NULL CHECK (cadence_attempt IN (1, 2)),
    failed_predecessor_attempt_id uuid
        REFERENCES cadence_workflow_attempts(attempt_id) ON DELETE RESTRICT,
    started_at timestamptz NOT NULL,
    completed_at timestamptz NOT NULL,
    epoch_sha256 char(64) NOT NULL
        CHECK (epoch_sha256 ~ '^[0-9a-f]{64}$'),
    binding_sha256 char(64) NOT NULL
        CHECK (binding_sha256 ~ '^[0-9a-f]{64}$'),
    scope_sha256 char(64) NOT NULL
        CHECK (scope_sha256 ~ '^[0-9a-f]{64}$'),
    eligible boolean NOT NULL,
    accepted boolean NOT NULL DEFAULT false,
    reason_code varchar(64) NOT NULL,
    retry_permitted boolean NOT NULL,
    created_at_db timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT fk_cadence_attempt_slot FOREIGN KEY (
        cadence_epoch_id, schedule_kind, slot_key
    ) REFERENCES cadence_workflow_slots (
        cadence_epoch_id, schedule_kind, slot_key
    ) ON DELETE RESTRICT,
    CONSTRAINT cadence_attempt_completion_order
        CHECK (completed_at >= started_at),
    CONSTRAINT cadence_attempt_acceptance
        CHECK (NOT accepted OR (eligible AND reason_code = 'accepted')),
    CONSTRAINT cadence_attempt_retry
        CHECK (NOT accepted OR NOT retry_permitted),
    CONSTRAINT cadence_attempt_branch CHECK (
        (cadence_attempt = 1 AND failed_predecessor_attempt_id IS NULL)
        OR (cadence_attempt = 2 AND failed_predecessor_attempt_id IS NOT NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cadence_accepted_slot
ON cadence_workflow_attempts (cadence_epoch_id, schedule_kind, slot_key)
WHERE accepted;
CREATE UNIQUE INDEX IF NOT EXISTS uq_cadence_workflow_run_attempt
ON cadence_workflow_attempts (
    workflow_file, workflow_run_id, workflow_run_attempt
);
CREATE TABLE IF NOT EXISTS cadence_attempt_source_receipts (
    attempt_id uuid NOT NULL
        REFERENCES cadence_workflow_attempts(attempt_id) ON DELETE RESTRICT,
    source_id uuid NOT NULL
        REFERENCES community_sources(id) ON DELETE RESTRICT,
    succeeded boolean NOT NULL,
    receipt_sha256 char(64) NOT NULL
        CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
    created_at_db timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (attempt_id, source_id)
);
"""

TABLES: Final = (
    "cadence_epoch_contracts",
    "cadence_workflow_slots",
    "cadence_workflow_attempts",
    "cadence_attempt_source_receipts",
)


def execute_schema(
    connection: Connection,
    *,
    offline: bool,
    alembic_execute: Callable[[TextClause], object],
) -> None:
    """Execute static DDL without treating regex clock colons as bind names."""
    for statement in SCHEMA_SQL.split(";\n"):
        if not statement.strip():
            continue
        if offline:
            _ = alembic_execute(text(statement.replace(":", r"\:")))
        else:
            _ = connection.exec_driver_sql(statement)


__all__ = ("SCHEMA_SQL", "TABLES", "execute_schema")
