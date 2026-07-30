"""Exact SQL statements used by the privacy database adapter."""

from sqlalchemy import text

LOCK = text("""
SELECT pg_advisory_xact_lock(
 hashtext('privacy-incident'),hashtext(CAST(:activation_nonce AS text))
)
""")
DB_TIME = text("SELECT transaction_timestamp()")
SCOPE = text("""
SELECT s.platform::text AS platform, e.id AS epoch_id
FROM community_sources s JOIN source_cadence_epochs e ON e.source_id=s.id
WHERE s.id=:source_id AND e.id=:epoch_id
  AND e.activation_nonce=:activation_nonce FOR UPDATE
""")
LATEST = text("""
SELECT id, state FROM source_activation_state_transitions
WHERE source_id=:source_id AND activation_nonce=:activation_nonce
ORDER BY transition_at_db DESC, id DESC LIMIT 1 FOR UPDATE
""")
TARGETS = text("""
WITH receipts AS (
 SELECT convert_from(canonical_receipt,'UTF8')::jsonb AS body
 FROM release_receipt_chain WHERE activation_nonce=:activation_nonce
)
SELECT 'workflow' AS kind, claimed_run_id AS value,
       'completed' AS status FROM release_operation_reservations
WHERE activation_nonce=:activation_nonce AND claimed_run_id IS NOT NULL
UNION ALL SELECT 'artifact', (body->>'artifact_id')::bigint, NULL
FROM receipts WHERE body ? 'artifact_id'
UNION ALL SELECT 'cache', body->>'cache_key', NULL
FROM receipts WHERE body ? 'cache_key'
ORDER BY kind, value
""")
DISABLE = text("""
UPDATE community_sources SET enabled=false, active_authorization_id=NULL,
 current_budget_id=NULL, current_binding_id=NULL, current_cadence_id=NULL
WHERE id=:source_id AND platform::text='manifold'
""")
INVALIDATE = text("""
UPDATE cadence_epoch_contracts SET invalidated_at=COALESCE(
 invalidated_at, transaction_timestamp()) WHERE cadence_epoch_id=:epoch_id
""")
APPEND_DEACTIVATED = text("""
INSERT INTO source_activation_state_transitions(
 id,activation_nonce,source_id,attestation_id,binding_intent_id,
 predecessor_transition_id,state,receipt_sha256)
SELECT gen_random_uuid(),activation_nonce,source_id,attestation_id,
 binding_intent_id,id,'deactivated',:receipt_sha256
FROM source_activation_state_transitions WHERE id=:transition_id
ON CONFLICT (activation_nonce,receipt_sha256) DO NOTHING
""")
DC_FINGERPRINT = text("""
SELECT count(*) AS rows, coalesce(max(p.updated_at)::text,'') AS edge
FROM posts p JOIN community_sources s ON s.id=p.source_id
WHERE s.platform::text='dcinside'
""")
PURGE = text("""
WITH exact_epoch AS (
 SELECT e.cadence_anchor_at AS starts_at,e.expires_at AS ends_at
 FROM source_cadence_epochs e WHERE e.id=:epoch_id
 AND e.source_id=:source_id AND e.activation_nonce=:activation_nonce
), affected_runs AS (
 SELECT r.id FROM collection_runs r,exact_epoch e WHERE r.source_id=:source_id
 AND r.created_at>=e.starts_at AND r.created_at<e.ends_at
), affected_items AS (
 SELECT i.id,i.post_version_id FROM page_commit_items i
 JOIN page_commits p ON p.id=i.page_commit_id
 WHERE p.run_id IN (SELECT id FROM affected_runs)
), affected_versions AS (
 SELECT DISTINCT post_version_id AS id FROM affected_items
 WHERE post_version_id IS NOT NULL
), dead_letter_rows AS (
 DELETE FROM analysis_queue WHERE post_version_id IN
 (SELECT id FROM affected_versions) RETURNING 1
), staging_rows AS (
 DELETE FROM post_matches WHERE post_version_id IN
 (SELECT id FROM affected_versions) RETURNING 1
), analyses_rows AS (
 DELETE FROM analyses WHERE post_version_id IN
 (SELECT id FROM affected_versions) RETURNING 1
), engagement_rows AS (
 DELETE FROM engagement_observations WHERE source_run_id IN
 (SELECT id FROM affected_runs) RETURNING 1
), raw_rows AS (
 DELETE FROM page_commit_items WHERE id IN
 (SELECT id FROM affected_items) RETURNING 1
), page_rows AS (
 DELETE FROM page_commits WHERE run_id IN
 (SELECT id FROM affected_runs) RETURNING 1
), content_rows AS (
 DELETE FROM post_versions WHERE id IN
 (SELECT id FROM affected_versions) RETURNING post_id
), empty_posts AS (
 DELETE FROM posts p WHERE p.source_id=:source_id AND NOT EXISTS
 (SELECT 1 FROM post_versions v WHERE v.post_id=p.id) RETURNING 1
)
SELECT (SELECT count(*) FROM dead_letter_rows)
 +(SELECT count(*) FROM staging_rows)+(SELECT count(*) FROM analyses_rows)
 +(SELECT count(*) FROM engagement_rows)+(SELECT count(*) FROM raw_rows)
 +(SELECT count(*) FROM page_rows)+(SELECT count(*) FROM content_rows)
 +(SELECT count(*) FROM empty_posts) AS deleted_row_count
""")
ZERO = text("""
WITH exact_epoch AS (
 SELECT cadence_anchor_at AS starts_at,expires_at AS ends_at
 FROM source_cadence_epochs WHERE id=:epoch_id AND source_id=:source_id
 AND activation_nonce=:activation_nonce
)
SELECT count(*) AS content_count,
 count(*) FILTER (WHERE v.content_hash IS NOT NULL OR p.canonical_url IS NOT NULL)
 AS title_body_url_hash_count
FROM posts p JOIN post_versions v ON v.post_id=p.id,exact_epoch e
WHERE p.source_id=:source_id AND v.collected_at>=e.starts_at
AND v.collected_at<e.ends_at
""")
VERIFY = text("""
SELECT (SELECT count(*)=0 FROM posts WHERE source_id=:source_id) AS content_zero,
 (SELECT count(*)=0 FROM post_versions v JOIN posts p ON p.id=v.post_id
  WHERE p.source_id=:source_id AND v.search_text<>'') AS search_zero,
 NOT s.enabled AS disabled,
 s.active_authorization_id IS NULL AND s.current_budget_id IS NULL
 AND s.current_binding_id IS NULL AND s.current_cadence_id IS NULL AS cleared,
 (SELECT version_num FROM alembic_version) AS revision,
 (SELECT state FROM source_activation_state_transitions
  WHERE activation_nonce=:activation_nonce ORDER BY transition_at_db DESC,id DESC
  LIMIT 1) AS latest_state,
 EXISTS(SELECT 1 FROM community_sources WHERE platform::text='dcinside'
  AND enabled) AS dcinside_intact
FROM community_sources s WHERE s.id=:source_id AND s.platform::text='manifold'
""")
APPEND_RESTORED = text("""
INSERT INTO source_activation_state_transitions(
 id,activation_nonce,source_id,attestation_id,binding_intent_id,
 predecessor_transition_id,state,receipt_sha256)
SELECT gen_random_uuid(),activation_nonce,source_id,attestation_id,
 binding_intent_id,id,'restored',:receipt_sha256
FROM source_activation_state_transitions
WHERE id=:transition_id AND state='restore_writing'
ON CONFLICT (activation_nonce,receipt_sha256) DO NOTHING
""")

__all__ = (
    "APPEND_DEACTIVATED",
    "APPEND_RESTORED",
    "DB_TIME",
    "DC_FINGERPRINT",
    "DISABLE",
    "INVALIDATE",
    "LATEST",
    "LOCK",
    "PURGE",
    "SCOPE",
    "TARGETS",
    "VERIFY",
    "ZERO",
)
