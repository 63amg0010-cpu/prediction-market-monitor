from collections.abc import Iterable
from pathlib import Path

import pytest
from app.db import Base, models
from sqlalchemy import (
    DateTime,
    LargeBinary,
    String,
    UniqueConstraint,
    insert,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import CompileError
from sqlalchemy.schema import CreateIndex, CreateTable

EXPECTED_TABLES = frozenset(
    {
        "admin_sessions",
        "analyses",
        "analysis_queue",
        "budget_decisions",
        "capability_proof_records",
        "collection_commands",
        "collection_runs",
        "collection_slots",
        "command_completions",
        "community_sources",
        "daily_report_versions",
        "daily_reports",
        "engagement_observations",
        "keyword_rule_sets",
        "keyword_rules",
        "login_rate_limits",
        "one_use_nonces",
        "page_commit_items",
        "page_commits",
        "post_matches",
        "post_versions",
        "posts",
        "principal_credential_versions",
        "provider_budget_records",
        "report_input_manifest_items",
        "report_input_manifests",
        "report_input_tombstones",
        "scheduled_job_runs",
        "scheduler_cursors",
        "service_principals",
        "source_authorization_decisions",
        "source_checkpoints",
        "source_publication_sequences",
        "source_run_publication_manifests",
        "verification_cursors",
        "verification_observations",
    }
)


def _column_sets(constraints: Iterable[UniqueConstraint]) -> set[frozenset[str]]:
    return {
        frozenset(column.name for column in constraint.columns)
        for constraint in constraints
    }


def test_schema_package_is_discoverable_when_phase_one_is_installed() -> None:
    # Given: the expected API application package location.
    db_package = Path(__file__).parents[2] / "app" / "db" / "__init__.py"

    # When: the Phase 1 schema package is inspected.
    package_exists = db_package.is_file()

    # Then: Phase 1 exposes an importable DB package.
    assert package_exists


def test_metadata_contains_every_phase_one_domain_table() -> None:
    # Given: importing the aggregate model module registers declarative tables.
    assert models is not None

    # When: the durable control-plane table names are read.
    table_names = frozenset(Base.metadata.tables)

    # Then: every required Phase 1 domain is represented.
    assert table_names >= EXPECTED_TABLES


def test_persisted_schema_excludes_author_and_raw_provider_fields() -> None:
    # Given: the complete registered metadata.
    forbidden_names = {
        "author",
        "author_id",
        "author_name",
        "profile",
        "profile_id",
        "raw_payload",
        "provider_payload",
    }

    # When: every persisted column is inspected.
    present_forbidden = {
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.name in forbidden_names or column.name.startswith("raw_")
    }

    # Then: privacy-prohibited representations do not exist.
    assert present_forbidden == set()


def test_critical_immutable_and_idempotency_keys_are_unique() -> None:
    # Given: tables whose identities must survive retries and revisions.
    expected = {
        "page_commits": {
            frozenset({"run_id", "page_idempotency_key"}),
            frozenset({"run_id", "page_ordinal"}),
        },
        "post_versions": {
            frozenset({"post_id", "revision"}),
            frozenset({"post_id", "content_hash"}),
        },
        "engagement_observations": {frozenset({"post_version_id", "source_run_id"})},
        "analysis_queue": {frozenset({"post_id", "post_version_id"})},
        "analyses": {
            frozenset(
                {
                    "post_version_id",
                    "prompt_version",
                    "model_version",
                    "schema_version",
                }
            )
        },
    }

    # When: declared unique constraints are normalized to column sets.
    actual = {
        table_name: _column_sets(
            constraint
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, UniqueConstraint)
        )
        for table_name in expected
    }

    # Then: every immutable/version/idempotency identity is database-enforced.
    assert all(expected[name] <= actual[name] for name in expected)


def test_source_enablement_and_finance_exclusivity_fail_closed() -> None:
    # Given: the source registry table.
    table = Base.metadata.tables["community_sources"]

    # When: checks and PostgreSQL indexes are compiled.
    dialect = postgresql.dialect()
    table_ddl = str(CreateTable(table).compile(dialect=dialect))
    index_ddl = "\n".join(
        str(CreateIndex(index).compile(dialect=dialect)) for index in table.indexes
    )

    # Then: enabled sources require authorization and Toss/Naver cannot coexist.
    assert "active_authorization_id" in table_ddl
    assert "enabled" in table_ddl
    assert "uq_community_sources_one_kr_finance_alternative" in index_ddl
    assert "toss_securities" in index_ddl
    assert "naver_finance" in index_ddl


def test_principal_credential_versions_preserve_symbolic_version() -> None:
    # Given
    table = Base.metadata.tables["principal_credential_versions"]

    # When
    version_type = table.c.version.type
    table_ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))

    # Then
    assert isinstance(version_type, String)
    assert version_type.length == 128
    assert "credential_version_nonblank" in table_ddl


def test_collection_run_terminal_marker_is_all_or_nothing() -> None:
    # Given: the server-owned collection run lifecycle.
    table = Base.metadata.tables["collection_runs"]
    required_columns = {
        "terminal_page_commit_id",
        "terminal_page_ordinal",
        "terminal_cursor",
        "terminal_reason",
        "terminal_chain_hash",
        "completion_ready_at",
        "finalized_at",
        "start_checkpoint_revision",
        "start_cursor",
        "genesis_chain_hash",
        "next_page_ordinal",
    }

    # When: terminal columns and lifecycle checks are inspected.
    table_ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))

    # Then: a success-capable run retains the persisted terminal proof atomically.
    assert required_columns <= set(table.columns.keys())
    assert "terminal_page_commit_id" in table_ddl
    assert "completion_ready_at" in table_ddl
    assert "succeeded" in table_ddl


def test_reports_retain_binary_value_complete_payloads_and_hashes() -> None:
    # Given: report projection and input-manifest storage.
    report = Base.metadata.tables["daily_report_versions"]
    manifest = Base.metadata.tables["report_input_manifests"]

    # When: payload storage types and identity fields are inspected.
    report_payload_type = report.c.report_payload.type
    manifest_payload_type = manifest.c.compressed_payload.type

    # Then: reproduction values are retained as bytes with independent hashes.
    assert isinstance(report_payload_type, LargeBinary)
    assert isinstance(manifest_payload_type, LargeBinary)
    assert report.c.report_payload_sha256.nullable is False
    assert manifest.c.manifest_payload_sha256.nullable is False
    assert manifest.c.input_set_hash.nullable is False
    assert manifest.c.uncompressed_byte_length.nullable is False


def test_manifest_rejects_mapping_as_canonical_payload_representation() -> None:
    # Given: an invalid JSON mapping offered where deterministic gzip bytes belong.
    manifest = Base.metadata.tables["report_input_manifests"]
    statement = insert(manifest).values(compressed_payload={"records": []})

    # When: PostgreSQL literal compilation crosses the binary type boundary.
    with pytest.raises(CompileError):
        _ = statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )

    # Then: malformed payload representation cannot compile as valid BYTEA.


def test_manifest_item_supports_restrictive_live_to_tombstone_switch() -> None:
    # Given: a value slice that initially references a live post version.
    item = Base.metadata.tables["report_input_manifest_items"]

    # When: its live and deletion-provenance columns are inspected.
    item_ddl = str(CreateTable(item).compile(dialect=postgresql.dialect()))

    # Then: exactly one live or tombstone post provenance is required per record.
    assert "post_version_tombstone_id" in item.columns
    assert "num_nonnulls" in item_ddl
    assert item.c.live_post_version_id.foreign_keys
    assert item.c.post_version_tombstone_id.foreign_keys


def test_all_tables_compile_with_postgresql_uuid_and_timezone_types() -> None:
    # Given: registered SQLAlchemy metadata and the PostgreSQL 15 dialect.
    dialect = postgresql.dialect()

    # When: every table is compiled to PostgreSQL DDL.
    ddl = "\n".join(
        str(CreateTable(table).compile(dialect=dialect))
        for table in Base.metadata.sorted_tables
    )

    # Then: compilation succeeds with native UUID and timezone-aware timestamps.
    assert "UUID" in ddl
    assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert "JSONB" in ddl
    assert all(
        column.type.timezone
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, DateTime)
    )
