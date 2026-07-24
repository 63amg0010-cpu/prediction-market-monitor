"""Keep the server-only public schema private on Supabase."""

from collections.abc import Sequence
from typing import Final

from alembic import op

revision: str = "20260724_0006"
down_revision: str | None = "20260723_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PUBLIC_TABLES: Final[tuple[str, ...]] = (
    "admin_sessions",
    "alembic_version",
    "analyses",
    "analysis_queue",
    "budget_decisions",
    "capability_proof_records",
    "collection_commands",
    "collection_runs",
    "collection_skip_observations",
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
    "report_input_manifest_item_matches",
    "report_input_manifest_item_tombstones",
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
    "verification_snapshot_sources",
    "verification_snapshot_uses",
    "verification_snapshots",
)

_TRIGGER_FUNCTIONS: Final[tuple[str, ...]] = (
    "monitor_reject_mutation",
    "monitor_reject_update",
    "monitor_require_active_source_authorization",
    "monitor_require_page_source_authorization",
    "monitor_require_running_source_authorization",
)

_DENIED_ROLES: Final[str] = "PUBLIC, anon, authenticated"
_SUPABASE_TABLE_PRIVILEGES: Final[str] = "ALL PRIVILEGES"

_FOREIGN_KEY_INDEXES: Final[tuple[tuple[str, str, str], ...]] = (
    ("ix_sources_active_authorization", "community_sources", "active_authorization_id"),
    ("ix_reports_latest_version", "daily_reports", "latest_version_id"),
    ("ix_admin_sessions_principal", "admin_sessions", "principal_id"),
    ("ix_budget_decisions_source", "budget_decisions", "source_id"),
    ("ix_budget_decisions_record", "budget_decisions", "budget_record_id"),
    ("ix_capability_proofs_principal", "capability_proof_records", "principal_id"),
    ("ix_collection_commands_slot", "collection_commands", "slot_id"),
    (
        "ix_report_versions_supersedes",
        "daily_report_versions",
        "supersedes_version_id",
    ),
    ("ix_report_versions_report", "daily_report_versions", "report_id"),
    ("ix_nonces_principal", "one_use_nonces", "principal_id"),
    ("ix_posts_current_version", "posts", "current_version_id"),
    ("ix_tombstones_source", "report_input_tombstones", "source_id"),
    ("ix_checkpoints_last_run", "source_checkpoints", "last_completed_run_id"),
    ("ix_snapshot_sources_source", "verification_snapshot_sources", "source_id"),
    ("ix_runs_claim_authorization", "collection_runs", "authorization_decision_id"),
    ("ix_runs_claim_budget", "collection_runs", "budget_decision_id"),
    ("ix_runs_terminal_page", "collection_runs", "terminal_page_commit_id"),
    ("ix_runs_skip_authorization", "collection_runs", "skip_authorization_decision_id"),
    ("ix_runs_skip_budget", "collection_runs", "skip_budget_decision_id"),
    ("ix_queue_post_version", "analysis_queue", "post_version_id"),
    ("ix_queue_lease_principal", "analysis_queue", "leased_by_principal_id"),
    (
        "ix_skip_observations_command",
        "collection_skip_observations",
        "command_id",
    ),
    ("ix_engagement_source_run", "engagement_observations", "source_run_id"),
    ("ix_page_commits_checkpoint", "page_commits", "checkpoint_id"),
    ("ix_page_commits_command", "page_commits", "command_id"),
    ("ix_post_matches_rule", "post_matches", "rule_id"),
    ("ix_page_items_post_version", "page_commit_items", "post_version_id"),
    (
        "ix_publications_terminal_page",
        "source_run_publication_manifests",
        "terminal_page_commit_id",
    ),
    ("ix_manifest_items_source", "report_input_manifest_items", "source_id"),
    (
        "ix_manifest_items_engagement",
        "report_input_manifest_items",
        "live_engagement_observation_id",
    ),
    (
        "ix_manifest_items_publication",
        "report_input_manifest_items",
        "live_source_publication_manifest_id",
    ),
    (
        "ix_manifest_items_post_tombstone",
        "report_input_manifest_items",
        "post_version_tombstone_id",
    ),
    (
        "ix_manifest_items_post_version",
        "report_input_manifest_items",
        "live_post_version_id",
    ),
    (
        "ix_manifest_items_analysis",
        "report_input_manifest_items",
        "live_analysis_id",
    ),
    (
        "ix_observations_latest_run",
        "verification_observations",
        "latest_successful_run_id",
    ),
    ("ix_observations_snapshot", "verification_observations", "snapshot_id"),
    (
        "ix_observations_publication",
        "verification_observations",
        "visible_publication_manifest_id",
    ),
    ("ix_observations_source", "verification_observations", "source_id"),
    (
        "ix_manifest_item_matches_match",
        "report_input_manifest_item_matches",
        "post_match_id",
    ),
    (
        "ix_manifest_item_tombstones_tombstone",
        "report_input_manifest_item_tombstones",
        "tombstone_id",
    ),
)


def upgrade() -> None:
    """Remove Data API access and index every previously uncovered foreign key."""
    for table_name in _PUBLIC_TABLES:
        table = f"public.{table_name}"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM {_DENIED_ROLES}")
    for function_name in _TRIGGER_FUNCTIONS:
        function = f"public.{function_name}()"
        op.execute(f"ALTER FUNCTION {function} SET search_path = public, pg_temp")
        op.execute(f"REVOKE EXECUTE ON FUNCTION {function} FROM {_DENIED_ROLES}")
    for index_name, table_name, column_name in _FOREIGN_KEY_INDEXES:
        op.create_index(
            index_name,
            table_name,
            [column_name],
            unique=False,
            if_not_exists=True,
        )


def downgrade() -> None:
    """Restore the pre-Supabase-hardening schema behavior."""
    for index_name, table_name, _ in reversed(_FOREIGN_KEY_INDEXES):
        op.drop_index(index_name, table_name=table_name, if_exists=True)
    for function_name in reversed(_TRIGGER_FUNCTIONS):
        function = f"public.{function_name}()"
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO anon, authenticated")
        op.execute(f"ALTER FUNCTION {function} RESET search_path")
    for table_name in reversed(_PUBLIC_TABLES):
        table = f"public.{table_name}"
        grant = f"GRANT {_SUPABASE_TABLE_PRIVILEGES} ON TABLE {table}"
        op.execute(f"{grant} TO anon, authenticated")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
