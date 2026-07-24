import re

from app.db.report_models import DailyReportVersion
from app.reporting.repository import (
    LOAD_RETAINED_REPORT,
    LOCK_DAILY_REPORT,
    LOCK_REPORT_DATE,
)
from app.reporting.retention_sql_orphans import LOCK_ORPHAN_TOMBSTONES
from app.reporting.retention_sql_statements import (
    ELIGIBLE_SOURCE_IDS,
    LOCK_ANALYSIS_REFERENCES,
    LOCK_ANALYSIS_SOURCE,
    LOCK_ENGAGEMENT_REFERENCES,
    LOCK_ENGAGEMENT_SOURCE,
    LOCK_MATCH_REFERENCES,
    LOCK_MATCH_SOURCE,
    LOCK_POST_VERSION_REFERENCES,
    LOCK_POST_VERSION_SOURCE,
    LOCK_PUBLICATION_REFERENCES,
    LOCK_PUBLICATION_SOURCE,
)
from app.reporting.sql_input_statements import (
    PUBLICATIONS_FOR_WINDOWS,
    REPORT_MATCHES,
    REPORT_RECORDS,
    RUNS_FOR_WINDOWS,
    SLOTS_FOR_WINDOWS,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import Constraint
from sqlalchemy.sql import ClauseElement

_TABLE_REFERENCE = re.compile(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)
_FORBIDDEN_REPLAY_TABLES = {
    "analyses",
    "collection_runs",
    "community_sources",
    "engagement_observations",
    "post_matches",
    "post_versions",
    "posts",
    "source_run_publication_manifests",
}


def _sql(statement: ClauseElement) -> str:
    return str(statement).lower()


def test_rp07_retained_replay_query_has_an_independent_source_table_guard() -> None:
    tables = set(_TABLE_REFERENCE.findall(_sql(LOAD_RETAINED_REPORT)))
    assert tables == {"daily_report_versions", "report_input_manifests"}
    assert tables.isdisjoint(_FORBIDDEN_REPLAY_TABLES)


def test_report_append_uses_transaction_lock_and_database_uniqueness() -> None:
    advisory_lock = _sql(LOCK_REPORT_DATE)
    latest_lock = _sql(LOCK_DAILY_REPORT)
    constraints = {
        item.name
        for item in DailyReportVersion.__table_args__
        if isinstance(item, Constraint)
    }
    assert "pg_advisory_xact_lock" in advisory_lock
    assert "for update" in latest_lock
    assert "uq_report_input_identity" in constraints


def test_rp08_retention_sql_locks_sources_and_live_references() -> None:
    source_locks = (
        LOCK_POST_VERSION_SOURCE,
        LOCK_ANALYSIS_SOURCE,
        LOCK_MATCH_SOURCE,
        LOCK_ENGAGEMENT_SOURCE,
        LOCK_PUBLICATION_SOURCE,
    )
    reference_locks = (
        LOCK_POST_VERSION_REFERENCES,
        LOCK_ANALYSIS_REFERENCES,
        LOCK_MATCH_REFERENCES,
        LOCK_ENGAGEMENT_REFERENCES,
        LOCK_PUBLICATION_REFERENCES,
    )

    for statement in source_locks:
        sql = _sql(statement)
        assert "where" in sql
        assert ":entity_id" in sql
        assert "for update of" in sql
    for statement in reference_locks:
        sql = _sql(statement)
        assert "report_input_manifest_item" in sql
        assert ":entity_id" in sql
        assert "for update of" in sql
    discovery = _sql(ELIGIBLE_SOURCE_IDS)
    assert "order by priority, retention_started_at, entity_id" in discovery
    assert "limit :limit" in discovery


def test_rp09_orphan_tombstone_sql_requires_expiry_and_no_references() -> None:
    sql = " ".join(
        str(
            LOCK_ORPHAN_TOMBSTONES.compile(
                dialect=postgresql.dialect(),
            )
        )
        .lower()
        .split()
    )

    assert "report_input_tombstones.retain_until <=" in sql
    assert sql.count("not (exists") == 2
    assert "report_input_manifest_items" in sql
    assert "report_input_manifest_item_tombstones" in sql
    assert "for update" in sql


def test_rp10_pq_sql_binds_windows_versions_and_latest_values() -> None:
    records = _sql(REPORT_RECORDS)
    assert "p.published_at >= :comparison_start" in records
    assert "p.published_at < :primary_end" in records
    for column in ("prompt_version", "model_version", "schema_version"):
        assert f"a.{column} = :{column}" in records
        assert f"q.{column} = :{column}" in records
    assert "order by e.observed_at desc, e.id desc" in records
    assert "order by publication_row.sequence desc" in records
    assert records.count("limit 1") == 2

    matches = _sql(REPORT_MATCHES)
    assert "pm.post_version_id = any" in matches
    assert "ruleset.version = any" in matches
    for statement in (
        SLOTS_FOR_WINDOWS,
        RUNS_FOR_WINDOWS,
        PUBLICATIONS_FOR_WINDOWS,
    ):
        windowed = _sql(statement)
        assert "due_slot_utc >= :comparison_start" in windowed
        assert "due_slot_utc < :primary_end" in windowed
