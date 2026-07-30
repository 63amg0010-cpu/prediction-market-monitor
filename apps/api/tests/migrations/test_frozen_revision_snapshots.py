from __future__ import annotations

import hashlib
import re
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config
from app.db import models as current_models
from sqlalchemy import Column, Integer, MetaData, Table

if TYPE_CHECKING:
    import pytest

API_ROOT = Path(__file__).parents[2]
ALEMBIC_INI = API_ROOT / "alembic.ini"
VERSIONS = API_ROOT / "migrations" / "versions"
SNAPSHOTS = API_ROOT / "frozen_migration_snapshots"
FROZEN_REVISIONS = (
    VERSIONS / "20260721_0001_initial_schema.py",
    VERSIONS / "20260722_0003_verification_snapshots.py",
    VERSIONS / "20260722_0004_claim_budget_skip_contracts.py",
)
EXPECTED_0001_DDL_SHA256 = (
    "c339c22f12b00aefd58196e3c8fb773d888114b976f0f1b7fed6e3411aca88ba"
)


def _offline_upgrade(revision: str) -> str:
    output = StringIO()
    config = Config(
        str(ALEMBIC_INI),
        stdout=output,
        output_buffer=output,
    )
    command.upgrade(config, revision, sql=True)
    return output.getvalue()


def _normalized_ddl(ddl: str) -> str:
    lines = (
        re.sub(r"\s+", " ", line).strip().removesuffix(",")
        for line in ddl.splitlines()
        if line.strip()
    )
    return "\n".join(sorted(lines))


def _segment(ddl: str, start: str, end: str) -> str:
    return ddl.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]


def test_0001_execution_does_not_consult_runtime_current_orm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_metadata = MetaData()
    _ = Table("runtime_future_leak_probe", runtime_metadata, Column("id", Integer))
    monkeypatch.setattr(current_models, "metadata", runtime_metadata)

    ddl = _offline_upgrade("20260721_0001")

    assert "CREATE TABLE community_sources" in ddl
    assert "runtime_future_leak_probe" not in ddl


def test_frozen_revisions_forbid_current_application_schema_imports() -> None:
    production_files = (*FROZEN_REVISIONS, *SNAPSHOTS.rglob("*.py"))

    for path in production_files:
        source = path.read_text(encoding="utf-8")
        assert "from app.db" not in source, path
        assert "from app.domain" not in source, path
        assert "import app.db" not in source, path
        assert "import app.domain" not in source, path


def test_0001_normalized_postgresql_ddl_matches_golden_hash() -> None:
    ddl = _offline_upgrade("20260721_0001")

    digest = hashlib.sha256(_normalized_ddl(ddl).encode()).hexdigest()

    assert digest == EXPECTED_0001_DDL_SHA256


def test_0001_stops_at_its_historical_revision_boundary() -> None:
    ddl = _offline_upgrade("20260721_0001")
    source_enum = _segment(ddl, "CREATE TYPE source_platform", ";")
    terminal_enum = _segment(ddl, "CREATE TYPE terminal_reason", ";")
    credentials = _segment(
        ddl,
        "CREATE TABLE principal_credential_versions",
        "CREATE TABLE admin_sessions",
    )
    runs = _segment(ddl, "CREATE TABLE collection_runs", "CREATE TABLE posts")
    budgets = _segment(
        ddl,
        "CREATE TABLE budget_decisions",
        "CREATE TABLE capability_proof_records",
    )
    observations = _segment(
        ddl,
        "CREATE TABLE verification_observations",
        "CREATE TABLE scheduled_job_runs",
    )

    assert "'manifold'" not in source_enum
    assert "'reviewed_byte_cap'" not in terminal_enum
    assert "version INTEGER NOT NULL" in credentials
    assert "CHECK (version > 0)" in credentials
    assert "collection_skip_observations" not in ddl
    assert "verification_snapshots" not in ddl
    assert "verification_snapshot_sources" not in ddl
    assert "verification_snapshot_uses" not in ddl
    assert "snapshot_id UUID NOT NULL" in observations
    assert "snapshot_published_at TIMESTAMP WITH TIME ZONE NOT NULL" in observations
    assert "fk_verification_observations_snapshot" not in observations
    assert "authorization_decision_id" not in runs
    assert "authorization_snapshot" not in runs
    assert "budget_decision_id" not in runs
    assert "budget_decision_status" not in runs
    assert "reviewed_page_cap" not in runs
    assert "reviewed_post_cap" not in runs
    assert "skip_authorization_decision_id" not in runs
    assert "skip_budget_decision_id" not in runs
    assert "policy_version" not in budgets
    assert "reviewed_page_cap" not in budgets
    assert "reviewed_post_cap" not in budgets
    assert "evidence_location" not in budgets
    assert "release_operations" not in ddl
    assert "release_receipts" not in ddl
    assert "collection_cadence_policies" not in ddl
    assert "DEFERRABLE INITIALLY DEFERRED" not in _segment(
        ddl,
        "ALTER TABLE daily_reports ADD CONSTRAINT fk_daily_reports_latest_version",
        ";",
    )
    assert "report_input_tombstones_first_manifest_id_fkey" in ddl
    assert "trg_verification_snapshots_immutable" not in ddl


def test_0003_and_0004_create_only_their_frozen_schema_additions() -> None:
    ddl_0003 = _offline_upgrade("20260722_0002:20260722_0003")
    ddl_0004 = _offline_upgrade("20260722_0003:20260722_0004")

    assert ddl_0003.count("CREATE TABLE IF NOT EXISTS verification_") == 3
    assert "CREATE TABLE IF NOT EXISTS verification_snapshots" in ddl_0003
    assert "CREATE TABLE IF NOT EXISTS verification_snapshot_sources" in ddl_0003
    assert "CREATE TABLE IF NOT EXISTS verification_snapshot_uses" in ddl_0003
    assert "fk_verification_observations_snapshot" in ddl_0003
    assert "trg_verification_snapshots_immutable" in ddl_0003
    assert "collection_skip_observations" not in ddl_0003

    assert "CREATE TABLE IF NOT EXISTS collection_skip_observations" in ddl_0004
    assert ddl_0004.count("ADD COLUMN IF NOT EXISTS") == 12
    assert "fk_runs_claim_authorization" in ddl_0004
    assert "fk_runs_claim_budget" in ddl_0004
    assert "fk_runs_skip_authorization" in ddl_0004
    assert "fk_runs_skip_budget" in ddl_0004
    assert "ck_collection_runs_single_skip_proof" in ddl_0004
    assert "verification_snapshots" not in ddl_0004
