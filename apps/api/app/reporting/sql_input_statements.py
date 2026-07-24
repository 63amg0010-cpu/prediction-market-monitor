"""PostgreSQL statements for repeatable-read P/Q report input assembly."""

from typing import Final

from sqlalchemy import TextClause, text

REPEATABLE_READ: Final[TextClause] = text(
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
)

DATABASE_CLOCK: Final[TextClause] = text("SELECT clock_timestamp() AS observed_at")

REPORT_RECORDS: Final[TextClause] = text(
    """
    SELECT s.id AS source_id, s.country::text AS country,
           s.platform::text AS platform, s.display_name AS community,
           pv.id AS post_version_id, pv.content_hash AS post_content_hash,
           p.published_at AS published_at_utc,
           a.id AS analysis_id, a.state::text AS analysis_state,
           a.output_hash, a.prompt_version, a.model_version, a.schema_version,
           a.analyzed_at, a.relevance, a.sentiment::text AS sentiment, a.topics,
           q.status::text AS queue_status,
           engagement.id AS engagement_observation_id,
           engagement.engagement_hash,
           engagement.observed_at AS engagement_observed_at,
           engagement.comments_count, engagement.upvote_or_score,
           publication.id AS publication_id,
           publication.run_id AS publication_run_id,
           publication.terminal_page_commit_id
               AS publication_terminal_page_commit_id,
           publication.sequence AS publication_sequence,
           publication.final_chain_hash AS publication_final_chain_hash,
           publication.post_set_hash AS publication_post_set_hash,
           publication.distinct_post_version_count
               AS publication_distinct_post_version_count,
           publication.zero_post AS publication_zero_post,
           publication.committed_at AS publication_committed_at
    FROM posts p
    JOIN post_versions pv ON pv.id = p.current_version_id
    JOIN community_sources s ON s.id = p.source_id
    LEFT JOIN analyses a
      ON a.post_version_id = pv.id
     AND a.prompt_version = :prompt_version
     AND a.model_version = :model_version
     AND a.schema_version = :schema_version
    LEFT JOIN analysis_queue q
      ON q.post_version_id = pv.id
     AND q.prompt_version = :prompt_version
     AND q.model_version = :model_version
     AND q.schema_version = :schema_version
    LEFT JOIN LATERAL (
        SELECT e.id, e.engagement_hash, e.observed_at,
               e.comments_count, e.upvote_or_score
        FROM engagement_observations e
        WHERE e.post_version_id = pv.id
        ORDER BY e.observed_at DESC, e.id DESC
        LIMIT 1
    ) engagement ON true
    JOIN LATERAL (
        SELECT publication_row.*
        FROM page_commit_items item
        JOIN page_commits page ON page.id = item.page_commit_id
        JOIN source_run_publication_manifests publication_row
          ON publication_row.run_id = page.run_id
        WHERE item.post_version_id = pv.id
        ORDER BY publication_row.sequence DESC, publication_row.id DESC
        LIMIT 1
    ) publication ON true
    WHERE s.scope_version = :scope_version
      AND p.published_at >= :comparison_start
      AND p.published_at < :primary_end
    ORDER BY p.published_at, pv.id, s.id
    """
)

REPORT_MATCHES: Final[TextClause] = text(
    """
    SELECT pm.post_version_id, pm.id AS match_id, pm.match_hash,
           ruleset.version AS rule_set_version,
           ruleset.rules_hash AS rule_set_hash,
           pm.normalized_phrase, pm.matched AS match_present,
           pm.category AS stored_category, rule.category AS rule_category
    FROM post_matches pm
    JOIN keyword_rules rule ON rule.id = pm.rule_id
    JOIN keyword_rule_sets ruleset ON ruleset.id = rule.rule_set_id
    WHERE pm.post_version_id = ANY(CAST(:post_version_ids AS uuid[]))
      AND ruleset.version = ANY(CAST(:rule_set_versions AS text[]))
    ORDER BY pm.post_version_id, pm.normalized_phrase, pm.id
    """
)

SOURCES_FOR_SCOPE: Final[TextClause] = text(
    """
    SELECT source.id AS source_id, source.country::text AS country,
           source.platform::text AS platform, source.display_name AS community,
           source.external_key,
           source.enabled AS source_enabled,
           decision.status::text AS authorization_status,
           decision.effective_at AS authorization_effective_at,
           decision.expires_at AS authorization_expires_at,
           decision.revoked_at AS authorization_revoked_at
    FROM community_sources source
    LEFT JOIN source_authorization_decisions decision
      ON decision.id = source.active_authorization_id
    WHERE source.scope_version = :scope_version
    ORDER BY source.id
    """
)

SLOTS_FOR_WINDOWS: Final[TextClause] = text(
    """
    SELECT due_slot_utc
    FROM collection_slots
    WHERE scope_version = :scope_version
      AND due_slot_utc >= :comparison_start
      AND due_slot_utc < :primary_end
    ORDER BY due_slot_utc
    """
)

RUNS_FOR_WINDOWS: Final[TextClause] = text(
    """
    WITH ranked AS (
        SELECT run.source_id, slot.due_slot_utc, run.status::text AS status,
               run.attempt, run.started_at, run.finished_at,
               row_number() OVER (
                   PARTITION BY run.command_id, run.source_id
                   ORDER BY run.attempt DESC, run.id DESC
               ) AS attempt_rank
        FROM collection_runs run
        JOIN collection_commands command ON command.id = run.command_id
        JOIN collection_slots slot ON slot.id = command.slot_id
        WHERE run.scope_version = :scope_version
          AND slot.due_slot_utc >= :comparison_start
          AND slot.due_slot_utc < :primary_end
    )
    SELECT source_id, due_slot_utc, status, attempt, started_at, finished_at
    FROM ranked
    WHERE attempt_rank = 1
    ORDER BY due_slot_utc, source_id
    """
)

PUBLICATIONS_FOR_WINDOWS: Final[TextClause] = text(
    """
    SELECT publication.id, publication.run_id, publication.source_id,
           publication.terminal_page_commit_id, publication.sequence,
           publication.final_chain_hash, publication.post_set_hash,
           publication.distinct_post_version_count, publication.zero_post,
           publication.committed_at, slot.due_slot_utc
    FROM source_run_publication_manifests publication
    JOIN collection_runs run ON run.id = publication.run_id
    JOIN collection_commands command ON command.id = run.command_id
    JOIN collection_slots slot ON slot.id = command.slot_id
    WHERE run.scope_version = :scope_version
      AND slot.due_slot_utc >= :comparison_start
      AND slot.due_slot_utc < :primary_end
    ORDER BY publication.source_id, publication.sequence, publication.id
    """
)
