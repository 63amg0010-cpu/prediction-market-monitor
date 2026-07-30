"""PostgreSQL trigger DDL for fail-closed and immutable invariants."""

from typing import Final

from .revision_0001_parts.columns import sql_expression

FUNCTION_DDL: Final[tuple[str, ...]] = (
    """
    CREATE FUNCTION monitor_require_active_source_authorization()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.enabled AND NOT EXISTS (
            SELECT 1
            FROM source_authorization_decisions decision
            WHERE decision.id = NEW.active_authorization_id
              AND decision.source_id = NEW.id
              AND decision.status = 'approved'
              AND decision.effective_at <= statement_timestamp()
              AND decision.expires_at > statement_timestamp()
              AND decision.revoked_at IS NULL
        ) THEN
            RAISE EXCEPTION 'active source authorization required'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE FUNCTION monitor_require_running_source_authorization()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.status = 'running' AND NOT EXISTS (
            SELECT 1
            FROM community_sources source
            JOIN source_authorization_decisions decision
              ON decision.id = source.active_authorization_id
             AND decision.source_id = source.id
            WHERE source.id = NEW.source_id
              AND source.enabled
              AND decision.status = 'approved'
              AND decision.effective_at <= statement_timestamp()
              AND decision.expires_at > statement_timestamp()
              AND decision.revoked_at IS NULL
        ) THEN
            RAISE EXCEPTION 'running source authorization required'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE FUNCTION monitor_require_page_source_authorization()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM collection_runs run
            JOIN community_sources source ON source.id = run.source_id
            JOIN source_authorization_decisions decision
              ON decision.id = source.active_authorization_id
             AND decision.source_id = source.id
            WHERE run.id = NEW.run_id
              AND run.status = 'running'
              AND source.enabled
              AND decision.status = 'approved'
              AND decision.effective_at <= statement_timestamp()
              AND decision.expires_at > statement_timestamp()
              AND decision.revoked_at IS NULL
        ) THEN
            RAISE EXCEPTION 'page source authorization required'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE FUNCTION monitor_reject_update()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'immutable row cannot be updated'
            USING ERRCODE = '55000';
    END;
    $$
    """,
    """
    CREATE FUNCTION monitor_reject_mutation()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'append-only row cannot be changed'
            USING ERRCODE = '55000';
    END;
    $$
    """,
)

AUTHORIZATION_TRIGGER_DDL: Final[tuple[str, ...]] = (
    """
    CREATE TRIGGER trg_community_sources_authorization
    BEFORE INSERT OR UPDATE ON community_sources
    FOR EACH ROW EXECUTE FUNCTION monitor_require_active_source_authorization()
    """,
    """
    CREATE TRIGGER trg_collection_runs_authorization
    BEFORE INSERT OR UPDATE ON collection_runs
    FOR EACH ROW EXECUTE FUNCTION monitor_require_running_source_authorization()
    """,
    """
    CREATE TRIGGER trg_page_commits_authorization
    BEFORE INSERT ON page_commits
    FOR EACH ROW EXECUTE FUNCTION monitor_require_page_source_authorization()
    """,
    """
    CREATE TRIGGER trg_source_authorization_append_only
    BEFORE UPDATE OR DELETE ON source_authorization_decisions
    FOR EACH ROW EXECUTE FUNCTION monitor_reject_mutation()
    """,
)

IMMUTABLE_UPDATE_TABLES: Final[tuple[str, ...]] = (
    "analyses",
    "budget_decisions",
    "capability_proof_records",
    "command_completions",
    "daily_report_versions",
    "engagement_observations",
    "keyword_rule_sets",
    "keyword_rules",
    "page_commit_items",
    "page_commits",
    "post_matches",
    "post_versions",
    "provider_budget_records",
    "report_input_manifests",
    "source_run_publication_manifests",
    "verification_observations",
)

IMMUTABLE_TRIGGER_DDL: Final[tuple[str, ...]] = tuple(
    f"""
    CREATE TRIGGER trg_{table}_immutable
    BEFORE UPDATE ON {table}
    FOR EACH ROW EXECUTE FUNCTION monitor_reject_update()
    """
    for table in IMMUTABLE_UPDATE_TABLES
)

TRIGGER_DDL: Final[tuple[str, ...]] = (
    *AUTHORIZATION_TRIGGER_DDL,
    *IMMUTABLE_TRIGGER_DDL,
)

DROP_TRIGGER_DDL: Final[tuple[str, ...]] = (
    "DROP TRIGGER IF EXISTS trg_community_sources_authorization ON community_sources",
    "DROP TRIGGER IF EXISTS trg_collection_runs_authorization ON collection_runs",
    "DROP TRIGGER IF EXISTS trg_page_commits_authorization ON page_commits",
    sql_expression(
        (
            "DROP TRIGGER IF EXISTS trg_source_authorization_append_only",
            "ON source_authorization_decisions",
        )
    ),
    *tuple(
        f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}"
        for table in IMMUTABLE_UPDATE_TABLES
    ),
)

DROP_FUNCTION_DDL: Final[tuple[str, ...]] = (
    "DROP FUNCTION IF EXISTS monitor_require_page_source_authorization()",
    "DROP FUNCTION IF EXISTS monitor_require_running_source_authorization()",
    "DROP FUNCTION IF EXISTS monitor_require_active_source_authorization()",
    "DROP FUNCTION IF EXISTS monitor_reject_update()",
    "DROP FUNCTION IF EXISTS monitor_reject_mutation()",
)
