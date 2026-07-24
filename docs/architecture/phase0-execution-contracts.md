# Phase 0 execution contracts

Status: **normative for implementation**  
Scope: the two execution blockers carried by the approved ralplan into Phase 0  
Decision date: 2026-07-20

This contract refines, and does not replace or weaken, these normative inputs:

- `.gjc/_session-019f7d9b-aeb4-7000-9ede-e0ccc4f7e8af/specs/deep-interview-prediction-market-community-monitor.md`
- `.gjc/_session-019f7eb3-93c8-7000-9a3b-59f4bb93c4ca/plans/ralplan/019f7eb3-93c8-7000-9a3b-59f4bb93c4ca/pending-approval.md`
- Architect pass 5: `.gjc/_session-019f7edc-7812-7000-bd52-5ebdb790018d/plans/ralplan/019f7edc-7812-7000-bd52-5ebdb790018d/stage-05-architect.md`
- Critic pass 5: `.gjc/_session-019f7edc-a006-7000-bfa2-44c67c59883a/plans/ralplan/019f7edc-a006-7000-bfa2-44c67c59883a/stage-05-critic.md`

`MUST`, `MUST NOT`, `SHOULD`, and `MAY` are used normatively. Database time is the only state-transition clock. UUIDs are lowercase canonical strings, hashes are lowercase SHA-256 hex, timestamps are UTC RFC 3339 with six fractional digits and `Z`, and all persisted nullable values retain `null` rather than being coerced to zero, false, neutral, or an empty string.

## 1. Terminal PageCommit and server-enforced completion

### 1.1 Chosen lifecycle

There is no additional run-finalize endpoint. `POST /v1/collector/commands/{command_id}/complete` calls one server-owned `finalize_command` transaction. That transaction locks the command and all runs for the current attempt, validates every submitted outcome against persisted facts, moves each active run from `running` to exactly one terminal run status, creates publications for successful runs, and then derives the command status. Validation failure writes none of those transitions.

A successful run MUST have a previously committed terminal `PageCommit`. The `complete` request cannot create, amend, or merely assert that marker. A self-consistent client-supplied cursor, page count, or chain without the persisted marker is insufficient and returns `409 terminal_page_missing`.

Run terminal statuses are `succeeded`, `failed_retryable`, `failed_terminal`, `skipped_policy`, and `skipped_quota`. `partial` is a command aggregate only. The existing aggregate table remains authoritative.

### 1.2 Run-start and PageCommit invariants

On claim, the server snapshots these immutable run-start values:

| Field | Invariant |
|---|---|
| `command_id`, `run_id`, `source_id`, `scope_version`, `attempt` | Bound to one claimed run and never reused by a retry run. |
| `start_checkpoint_revision`, `start_cursor` | Values read while locking `SourceCheckpoint(source_id, scope_version)`. |
| `genesis_chain_hash` | Computed as specified below; it is also the chain head for a run with no commits. |
| `next_page_ordinal` | Starts at `0`; ordinals are contiguous and zero-based within a run. |
| `terminal_page_commit_id` | Starts `null`; once set it is immutable and seals the run's page stream. |

At most one run may be active for a `(source_id, scope_version)` checkpoint; a database partial unique constraint over `created|running` runs and the claim transaction enforce this. A later run cannot advance a checkpoint while an earlier run is awaiting finalization.

Every new page request retains the ralplan fields and adds the following required terminal evidence:

```json
{
  "command_id": "uuid",
  "attempt": 1,
  "lease_token": "base64url-random",
  "page_idempotency_key": "uuid",
  "expected_checkpoint_revision": 12,
  "expected_cursor": "opaque-or-null",
  "next_cursor": "opaque-or-null",
  "page_ordinal": 3,
  "posts": [],
  "source_page_item_count": 0,
  "source_page_receipt_sha256": "sha256",
  "page_fetch_started_at": "RFC3339-UTC",
  "page_fetch_finished_at": "RFC3339-UTC",
  "is_terminal_page": true,
  "terminal_reason": "source_exhausted"
}
```

`terminal_reason` MUST be `null` when `is_terminal_page=false`. When true it MUST be one of:

- `source_exhausted`: the reviewed adapter obtained a successful authorized response whose documented pagination signal says that this collection span has no next page. `source_page_receipt_sha256` binds the redacted response envelope and pagination signal; no raw provider payload or author data is retained.
- `reviewed_page_cap`: the server verifies that the effective immutable page cap is reached.
- `reviewed_post_cap`: the server verifies that the effective immutable post cap is reached.

A cap reason supplied before the server-observed cap is `422 invalid_terminal_reason`. Policy, authorization, quota, transport, parse, and provider errors are not terminal pages; they produce skip/failure outcomes instead. A terminal cursor MAY be non-null because a capped run must resume later and some exhausted adapters use a watermark cursor.

For every new commit, the server MUST enforce all of the following in one transaction:

1. Validate JWT scope/expiry, principal, command/run/source/attempt, active authorization, current lease hash, and `running` state.
2. Lock `CollectionRun`, `SourceCheckpoint`, and the run's chain head.
3. Require `page_ordinal == next_page_ordinal`, exact checkpoint revision/cursor CAS, and no existing terminal commit.
4. Recompute every post content hash from the normalized persisted values, reject author/raw fields, validate limits, and produce an ordered per-item result (`accepted`, `duplicate`, or `rejected_oversize`).
5. Insert the immutable PageCommit and its item results; write/upsert posts, versions, engagement observations, matches, and queue rows; update counts; advance the checkpoint cursor/revision/watermark; and persist the exact response.
6. If terminal, set the run's immutable terminal commit ID, ordinal, cursor, reason, chain hash, and `completion_ready_at`. The run remains `running` until `finalize_command` succeeds.

The checkpoint's cursor, revision, and watermark are mutated only by this transaction. Finalization MAY set `last_completed_run_id` but MUST NOT change cursor, revision, or watermark.

### 1.3 Canonical hashes and chain

The server computes three separate hashes; no client-supplied digest substitutes for them.

1. `page_request_hash` is SHA-256 of canonical `page-request/v1` JSON containing the semantic request fields from `command_id` through `terminal_reason`, including the ordered normalized post/rejection descriptors. It excludes JWT, `lease_token`, and `page_idempotency_key`, allowing an authenticated response-recovery retry without changing semantics.
2. `page_content_hash` is SHA-256 of canonical `page-result/v1` JSON containing `page_request_hash`, the ordered server item outcomes and persisted IDs/hashes, accepted/duplicate/rejected counts, resulting checkpoint revision/cursor, ordinal, terminal flag, and terminal reason.
3. The chain is:

```text
genesis = SHA256("page-chain-genesis/v1\n" || JCS({run_id, command_id, source_id,
          scope_version, attempt, start_checkpoint_revision, start_cursor}))
link[n] = SHA256("page-chain-link/v1\n" || raw32(link[n-1]) || raw32(page_content_hash[n]))
```

JCS means the canonicalization rules in section 2.5. Because the terminal flag, reason, cursor, ordinal, and result are inside `page_content_hash`, the final chain cryptographically binds them. `committed_page_hash_chain` always means the final link, or genesis only for a non-successful zero-commit run.

### 1.4 Zero-page and zero-post runs

- A successful run with zero `PageCommit` rows is impossible.
- If an adapter's first successful fetch/preflight result contains no data page, it commits one empty terminal PageCommit at ordinal `0`, with unchanged or adapter-derived `next_cursor`, zero item/accepted/duplicate/rejected counts, and `terminal_reason=source_exhausted`. Thus the run has zero data pages but one auditable terminal commit.
- A fetched terminal page MAY contain zero normalized posts, only duplicates, or only oversize rejections. Those outcomes remain in the page result and chain.
- `SourceRunPublicationManifest.zero_post` is true exactly when the union of persisted post-version IDs observed by the run is empty. Duplicates with a persisted post version make it false; oversize rejection descriptors do not.
- `post_set_hash` hashes the sorted distinct persisted post-version IDs and content hashes observed by the run. For the empty set it is the defined SHA-256 of canonical `[]`, never `null`.

### 1.5 Page idempotency and conflicts

After authentication and command/run/attempt ownership checks, an existing `(run_id, page_idempotency_key)` is handled before current-state/CAS checks:

- Same `page_request_hash` and original lease identity: return `200` with the byte-equivalent stored success response, even if later pages or completion have advanced state.
- Different `page_request_hash`: return `409 idempotency_key_reused`; change nothing.
- A new key targeting an already committed ordinal: return `409 ordinal_already_committed` with existing commit ID, ordinal, and current checkpoint details; change nothing.

The first successful commit returns `201`. Other mandatory failures are:

| Status/code | Condition | Observable state |
|---|---|---|
| `401 invalid_or_expired_token` | JWT invalid/expired | No write. |
| `403 source_authorization_inactive` | authorization revoked/out of scope | No write. |
| `409 lease_or_attempt_mismatch` | wrong lease, attempt, command/run relation, or superseded run | No write. |
| `409 checkpoint_conflict` | expected revision or cursor differs | Current revision/cursor returned; no write. |
| `409 ordinal_gap` | ordinal is not the next contiguous ordinal | Expected ordinal returned; no write. |
| `409 run_stream_sealed` | any new commit follows a terminal commit | Terminal commit details returned; no write. |
| `422 invalid_contract` | malformed cursor/timestamp/item/hash/limit/terminal combination | No write. |

On any `409`, the collector reloads `GET /v1/collector/runs/{run_id}/checkpoint`; it never guesses a cursor, skips an ordinal, or rewrites a commit.

### 1.6 Exact completion contract

The completion body is:

```json
{
  "completion_idempotency_key": "uuid",
  "attempt": 1,
  "lease_token": "base64url-random",
  "source_outcomes": [{
    "run_id": "uuid",
    "terminal_status": "succeeded",
    "last_page_commit_id": "uuid",
    "final_cursor": "opaque-or-null",
    "final_page_ordinal": 3,
    "committed_page_count": 4,
    "committed_page_hash_chain": "sha256",
    "skip_decision_id": null,
    "failure": null
  }]
}
```

The array MUST contain exactly one outcome for every run belonging to the current command attempt and no other run, sorted by `run_id` for request hashing. The server recomputes all page counts and chains; submitted values are comparison assertions only.

For `succeeded`, `last_page_commit_id`, `final_page_ordinal`, `committed_page_count`, and `committed_page_hash_chain` are non-null; `final_cursor` is present but may contain JSON null; `skip_decision_id` and `failure` MUST be null. The server requires:

- the stored terminal commit exists, is the latest contiguous ordinal, and has a valid terminal reason;
- commit count is `final_page_ordinal + 1`, the chain recomputes through every commit without a gap, and the submitted ID/ordinal/count/chain equal persisted values;
- terminal `next_cursor`, submitted `final_cursor`, and locked checkpoint cursor are exactly equal, and the checkpoint revision is the revision produced by that terminal commit;
- no uncommitted page reservation exists and all page transactions have committed.

For `skipped_policy` or `skipped_quota`, `last_page_commit_id` and `final_page_ordinal` MUST be null, committed count MUST be `0`, the submitted chain MUST equal genesis, `final_cursor` MUST equal `start_cursor`, `skip_decision_id` MUST reference a current server-owned authorization/policy or hard-budget decision recorded before source fetch, and `failure` MUST be null. A client-only skip reason is rejected.

For `failed_retryable` or `failed_terminal`, `failure={class,code,fingerprint,observed_at,retry_after_at|null}` is required, `skip_decision_id` MUST be null, and the class must match the server allowlist and retryability policy. A run may have zero or more contiguous nonterminal commits. Its submitted cursor/count/ordinal/chain and `last_page_commit_id` MUST match the persisted partial chain; at count zero the ID/ordinal are null, cursor equals `start_cursor`, and chain equals genesis. A run with a terminal commit cannot be finalized as failure.

`finalize_command` validates every outcome first and then atomically:

1. transitions all active runs to their terminal statuses;
2. for each success, increments the source-local publication sequence once, creates `SourceRunPublicationManifest`, and sets checkpoint `last_completed_run_id` without changing checkpoint cursor/revision;
3. derives the mutually exclusive command aggregate and persists its response and completion request hash.

The completion request hash is JCS over `attempt` and sorted `source_outcomes`, excluding the lease token and idempotency key. After principal/command/attempt ownership checks, an existing completion key is resolved before current-state checks. First success returns `200`. The same key and request hash returns the identical stored `200` response after completion; reusing the key with a changed hash returns `409 completion_idempotency_mismatch`. Any missing marker, final cursor/ordinal/count/chain mismatch, missing/extra run, invalid skip/failure proof, or non-current attempt returns a specific `409`; the command and every run stay pre-finalization.

### 1.7 Retries, races, and crashes

- Crash before the page transaction commits: no PageCommit/checkpoint change exists; the same key is retried and receives `201` if it commits.
- Crash after commit but before response: the same key/payload receives the stored `200`; no duplicate post, observation, queue row, count, or cursor advance occurs.
- Crash after one or more nonterminal commits: committed pages/checkpoint remain. Reconciliation terminalizes the abandoned run as retryable/terminal according to the existing attempt policy. A new attempt creates a new run, snapshots the persisted checkpoint, resets ordinal to `0`, and starts a new genesis chain; chains never cross runs.
- Crash after terminal commit but before completion: the run is marked completion-ready, not stale-abandoned. A retried `complete`, or the server reconciler invoking the same `finalize_command` rules, finalizes success from the persisted terminal marker. The reconciler never invents a marker.
- Concurrent page calls serialize on the run/checkpoint locks; exactly one next ordinal/CAS can commit. Losers receive deterministic `409` and reload.
- A failure or revocation after partial commits never rolls back durable pages. It prevents new commits/final success; a later authorized retry resumes only from the persisted checkpoint.

## 2. Self-contained formula-effective report retention

### 2.1 Chosen retention model

Every `DailyReportVersion` and its `ReportInputManifest` are created in the same repeatable-read transaction. The manifest contains a body-free, author-free, **value-bearing snapshot** sufficient to recompute the report. IDs and hashes remain provenance, but formulas consume the retained values, never a deleted source row and never a value recovered from a hash.

The canonical snapshot, report version, manifest items, and needed tombstones are retained until `retain_until = created_at + 180 days`. Shorter-lived post/version, analysis, match, engagement, run, and source-publication rows MAY be deleted after their own retention gates only after the tombstone transaction in section 2.6 succeeds.

### 2.2 Windows and P/Q roles

For report date `D` in `Asia/Seoul`:

- primary `P = [D 00:00:00+09:00, (D+1) 00:00:00+09:00)`;
- comparison `Q = [(D-1) 00:00:00+09:00, D 00:00:00+09:00)`.

Both UTC boundary timestamps and the Seoul dates are retained. Each post-version record has exactly one `role` (`primary` or `comparison`) determined by its retained publication timestamp. No record may appear twice within a role. P affects displayed candidate/pending/coverage totals and current metrics; Q affects comparison counts, deltas, highlights, and rising-keyword rates. Any value change in either role changes `input_set_hash` and therefore creates a correction revision.

The repeatable-read transaction selects inputs deterministically: the visible `posts.current_version_id` for each accepted non-oversize post published in P or Q; the analysis for that exact version and the configured prompt/model/schema tuple, or its exact non-valid state; all matches for the configured immutable rule sets; the latest engagement observation by `(observed_at, id)`; and source runs/publications visible for due slots inside each window. A later post revision, analysis, match, engagement observation, or source publication is a changed P/Q input. Transaction time itself is not an input, so reconciliation with no changed source facts reproduces the same `input_set_hash`.

### 2.3 Exact retained value schema

The decompressed manifest is one `report-input-manifest/v1` JSON object containing these value-bearing sections.

| Section | Exact retained values |
|---|---|
| Identity | report date and schema version; both window dates and UTC boundaries; source scope version. Report/manifest IDs, revision, `created_at`, and `retain_until` are row metadata outside input identity. |
| Governing definitions | formula version/hash and the literal formula constants; metric version/hash; category version/hash; sorted rule-set and analysis/model/prompt/schema version tuples. |
| Category mappings | every effective `{input_kind: rule|topic, rule_or_topic_key, version, normalized_value, category}` mapping; unmapped inputs explicitly map to `uncategorized`. |
| Record dimensions | ordinal, role, source ID, country, platform/community, post-version ID/content hash, publication UTC timestamp and Seoul date. No title, body, URL, author/profile, or provider payload. |
| Analysis | `analysis_state`, analysis ID/hash/version tuple or explicit nulls, analyzed timestamp or null, `relevance: true|false|null`, and `sentiment: positive|neutral|negative|null`. |
| Rule matches | sorted entries of match ID/hash, rule ID/set/version, normalized phrase, retained match-present boolean, and mapped category. |
| Topic matches | sorted entries of normalized topic key/value, analysis/schema version, and mapped category. |
| Effective categories | sorted unique categories produced by retained rule/topic mappings; a valid relevant record with no mapped input has `['uncategorized']`. |
| Engagement | selection state, observation ID/hash or null, observed timestamp or null, `comments_count: integer|null`, and `upvote_or_score: integer|null`. |
| Source coverage | per role/source dimensions; expected/enabled booleans; collection status; expected/success/failure/skip run counts; candidate, valid-analysis, pending, and relevant counts; cutoff publication sequence/manifest ID/hash or null; latest successful run start/finish, latest publication commit, latest attempt finish, and status-observed timestamps or null; coverage numerator/denominator and exact decimal ratio string or null. |

`analysis_state` is exactly one of `valid`, `pending`, `blocked_capability`, `failed_retryable`, `failed_terminal`, or `invalid_output`. Only `valid` permits non-null relevance. Only a valid relevant record permits non-null sentiment in formulas; missing analysis is never neutral or irrelevant. Provenance IDs/hashes/version tuple are all non-null for `valid` and all analysis-value fields are null otherwise.

Engagement `selection_state` is `selected` or `unavailable`. `unavailable` requires all observation fields and both numeric values to be null. `selected` requires ID/hash/time but each numeric value independently remains nullable. Null never contributes zero.

Collection status is exactly one of `complete`, `partial`, `missing`, `skipped_policy`, `skipped_quota`, `failed_retryable`, `failed_terminal`, or `unauthorized`. For an expected source/window it is derived in this order: inactive required authorization gives `unauthorized`; all expected runs succeeded gives `complete`; no recorded terminal outcome gives `missing`; a mixture containing any success gives `partial`; otherwise an all-same skip set gives its skip status; otherwise any terminal failure gives `failed_terminal`; otherwise any retryable/abandoned failure gives `failed_retryable`; all other mixtures give `partial`. Expected run count is the materialized due-slot count in that window. The retained status and event timestamps are the values used by the report; generated reconciliation time is not substituted, and later state cannot rewrite an existing manifest.

### 2.4 Formula projection

The manifest embeds the following literal policy, so replay does not require a configuration query:

- `candidate_count(P)` is the number of P records.
- `valid_analysis_count(P)` counts `analysis_state=valid`; `pending_count` is candidate minus valid. Analysis coverage is the exact fraction `valid/candidate`; it is null when candidate is zero.
- Relevant and sentiment counts use only valid records with `relevance=true`; null sentiment is excluded and counted separately as unknown.
- A category count is the number of relevant records containing that category in their deduplicated `effective_categories`. P/Q category delta is `P-Q`; delta rate is null when Q is zero, otherwise `(P-Q)/Q`.
- Primary category net sentiment is positive count minus negative count. Highlights keep at most five categories ranked by P relevant count descending, net sentiment descending, then category ascending.
- A rising phrase count is the number of relevant records with that normalized phrase, once per record. P phrases with count at least three are eligible. Rate is null when Q is zero, otherwise `(P-Q)/Q`; rank numeric rate descending with null last, then P count descending, then phrase ascending; keep at most ten.
- Engagement separately returns each numeric sum, known-value count, and unknown-value count over relevant P records. A sum is null when its known-value count is zero.
- Overall report status is `complete` only when every expected primary source has collection status `complete` and either candidate count is zero or analysis coverage is at least the exact fraction `85/100`; otherwise it is `partial`. An honestly complete empty collection has zero counts, null ratios/deltas/engagement sums, and empty ranked arrays.

Source coverage entries retain their already-counted scalars and are cross-checked against records during replay. A mismatch is corruption, not a reason to query the source tables.

### 2.5 Canonical serialization and identity

Before serialization, strings are Unicode NFC; timestamps, UUIDs, enum casing, integer ranges, explicit nulls, and array order are validated. Integers are JSON integers and remain below `2^53`; ratios are reduced numerator/denominator pairs plus a non-exponent decimal string, so floating-point output is never an identity input.

Arrays use these orders:

- windows: primary, comparison;
- records: role (primary first), then `post_version_id`, `source_id`;
- rule/topic mappings and matches: kind, normalized value, version, stable ID;
- categories/phrases/version tuples: Unicode code-point ascending;
- source coverage: role (primary first), then `source_id`.

The validated input object is serialized with RFC 8785 JSON Canonicalization Scheme (JCS) to UTF-8 without BOM or trailing newline. It expressly excludes report/manifest database IDs, revision, `created_at`, `retain_until`, compression metadata, and any other generated value, so identical source facts have identical identity.

```text
manifest_payload_sha256 = SHA256(canonical_manifest_bytes)
input_set_hash = SHA256("report-input-manifest/v1\n" || canonical_manifest_bytes)
```

The canonical bytes are stored in deterministic gzip form (`mtime=0`, no filename/comment); the two hashes cover the uncompressed bytes, so compressor changes cannot alter identity. The manifest row separately stores report ID/revision, `created_at`, `retain_until`, codec, uncompressed byte length, both hashes, and compressed payload. `ReportInputManifestItem` stores item kind (`record|source_coverage`), ordinal/role, the relevant provenance IDs/hashes, `value_slice_sha256`, nullable live FKs, and nullable tombstone FKs. Its value slice is the corresponding canonical record/coverage object; it MUST match the immutable payload.

The report projection is separately JCS-serialized as `daily-report-payload/v1`; its hash excludes database IDs and insertion timestamps and includes every displayed count, null, status, timestamp, highlight, rising phrase, and source-coverage value. Reproduction requires byte-equal projection bytes and hash.

### 2.6 Tombstone lifecycle and deletion order

`ReportInputTombstone` is deletion provenance, not a substitute formula store. It contains:

```text
entity_kind, source_entity_id, source_entity_hash, source_id,
published_or_observed_at|null, deleted_at, deletion_reason,
manifest_value_slice_sha256, first_manifest_id, retain_until
```

It contains no title, body, URL, author/profile, raw provider payload, or free text. Tombstones deduplicate on `(entity_kind, source_entity_id, source_entity_hash, manifest_value_slice_sha256)` and may be shared by many manifest items.

For any source row referenced by a retained manifest, cleanup MUST in one transaction:

1. lock the source row, every referencing manifest/item, and any matching tombstone;
2. verify the source hash, manifest payload hash, and item value-slice hash;
3. prove that the self-contained payload holds every required non-null and null formula value from section 2.3;
4. create/reuse the tombstone and switch the item's restrictive live FK to its tombstone FK;
5. only then delete the source row.

Any absent value, hash mismatch, unknown schema, or unswitched reference aborts cleanup and preserves the source row. Deleting a source row never mutates the canonical manifest or `input_set_hash`.

Reports, manifests, items, and referenced tombstones cannot be removed before `retain_until`. At expiry, deletion order is report pointer/version dependencies, manifest items, manifest payload, then tombstones with zero remaining references. A shared tombstone remains until the maximum `retain_until` of all referencing manifests. Source cleanup order remains tombstone-switch first, then eligible queue/history/page/run rows, then 30-day post/version/body rows; no FK is bypassed or cascaded across a retained manifest.

### 2.7 Reproduction with no deleted-row queries

`reproduce_report(manifest_id)` MUST perform only these steps:

1. Read the retained `ReportInputManifest` payload and the stored report projection/hash. Do not load post, post-version, analysis, match, engagement, source-run, publication-manifest, checkpoint, or source-coverage source tables.
2. Decompress with the recorded codec; verify length, `manifest_payload_sha256`, `input_set_hash`, schema, ordering, invariants, mappings, explicit null states, and P/Q windows.
3. Recompute record-derived and source-coverage cross-check counts using only payload values and the literal formula policy in section 2.4.
4. Construct the `daily-report-payload/v1` projection, JCS-serialize it, and require byte equality and hash equality with the stored report projection.
5. Return `reproduced`; on any difference return an immutable `manifest_corrupt` result and do not fall back to deleted/live source rows or the previously stored scalar columns.

The post-purge integration test MUST physically delete all eligible value-bearing dependency rows and install a query guard that fails the test if reproduction issues SQL against any forbidden table. Merely comparing a stored output hash is not reproduction.

## 3. Required implementation test matrix

These are future implementation gates, not claims that product tests already exist or pass. Each invocation MUST produce the named non-empty artifact.

| ID | Exact scenario and invocation | Binary observable | Required artifact |
|---|---|---|---|
| PC-01 | `uv run --package monitor-api pytest apps/api/tests/integration/test_page_commit.py::test_success_requires_persisted_terminal_commit` | `409 terminal_page_missing`; run/command remain running | `.omo/evidence/phase2/PC-01.json` |
| PC-02 | `uv run --package monitor-api pytest apps/api/tests/integration/test_page_commit.py::test_terminal_binds_cursor_ordinal_chain` | altered cursor, ordinal, flag, or link each returns 409; no transitions | `.omo/evidence/phase2/PC-02.json` |
| PC-03 | `uv run --package monitor-api pytest apps/api/tests/integration/test_page_commit.py::test_zero_data_page_success` | ordinal-0 empty terminal commit; success manifest has `zero_post=true` | `.omo/evidence/phase2/PC-03.json` |
| PC-04 | `uv run --package monitor-api pytest apps/api/tests/integration/test_page_commit.py::test_zero_post_duplicate_and_rejection_semantics` | zero-post/post-set/hash rules exactly match section 1.4 | `.omo/evidence/phase2/PC-04.json` |
| PC-05 | `uv run --package monitor-api pytest apps/api/tests/integration/test_page_commit.py::test_page_idempotent_response_loss` | first 201, replay 200 byte-equal; one cursor advance | `.omo/evidence/phase2/PC-05.json` |
| PC-06 | `uv run --package monitor-api pytest apps/api/tests/integration/test_page_commit.py::test_idempotency_payload_mismatch` | 409 and unchanged row/count/checkpoint | `.omo/evidence/phase2/PC-06.json` |
| PC-07 | `uv run --package monitor-api pytest apps/api/tests/integration/test_page_commit.py::test_cas_ordinal_lease_and_sealed_conflicts` | each conflict code is exact; no partial write | `.omo/evidence/phase2/PC-07.json` |
| PC-08 | `uv run --package monitor-api pytest apps/api/tests/integration/test_collection_commands.py::test_complete_atomically_terminalizes_runs` | all runs/publications/command commit together or none do | `.omo/evidence/phase2/PC-08.json` |
| PC-09 | `uv run --package monitor-api pytest apps/api/tests/integration/test_collection_commands.py::test_skip_and_partial_failure_guards` | skip requires server decision; partial failure retains commits/checkpoint | `.omo/evidence/phase2/PC-09.json` |
| PC-10 | `uv run --package monitor-api pytest apps/api/tests/integration/test_collection_commands.py::test_crash_before_and_after_terminal_commit` | nonterminal crash starts new run at checkpoint; terminal crash finalizes persisted marker | `.omo/evidence/phase2/PC-10.json` |
| PC-11 | `uv run --package monitor-api pytest apps/api/tests/integration/test_page_commit.py::test_revocation_and_cap_terminal_reason` | revoked source is 403; premature cap reason is 422 | `.omo/evidence/phase2/PC-11.json` |
| RP-01 | `uv run --package monitor-api pytest apps/api/tests/integration/test_daily_reports.py::test_manifest_snapshots_all_formula_values_and_nulls` | schema rejects each omitted scalar/null state | `.omo/evidence/phase4/RP-01.json` |
| RP-02 | `uv run --package monitor-api pytest apps/api/tests/integration/test_daily_reports.py::test_primary_and_comparison_change_identity` | any P or Q scalar/mapping change yields a new input hash/revision | `.omo/evidence/phase4/RP-02.json` |
| RP-03 | `uv run --package monitor-api pytest apps/api/tests/integration/test_daily_reports.py::test_categories_rules_topics_and_ties` | mappings, uncategorized, dedupe, ranks, and ties match section 2.4 | `.omo/evidence/phase4/RP-03.json` |
| RP-04 | `uv run --package monitor-api pytest apps/api/tests/integration/test_daily_reports.py::test_engagement_nulls_and_empty_windows` | unknown is never zero; exact null/empty/status output | `.omo/evidence/phase4/RP-04.json` |
| RP-05 | `uv run --package monitor-api pytest apps/api/tests/integration/test_daily_reports.py::test_source_coverage_status_and_timestamps` | retained source counts/status/timestamps reproduce exactly | `.omo/evidence/phase4/RP-05.json` |
| RP-06 | `uv run --package monitor-api pytest apps/api/tests/integration/test_daily_reports.py::test_jcs_hash_is_order_and_compressor_stable` | golden canonical bytes/hash match; gzip variation cannot change identity | `.omo/evidence/phase4/RP-06.json` |
| RP-07 | `uv run --package monitor-api pytest apps/api/tests/integration/test_daily_reports.py::test_post_purge_reproduction_forbids_source_queries` | dependencies physically absent, query guard sees zero forbidden SQL, report bytes equal | `.omo/evidence/phase4/RP-07.json` |
| RP-08 | `uv run --package monitor-api pytest apps/api/tests/integration/test_retention.py::test_cleanup_fails_closed_on_missing_value_or_hash` | cleanup rolls back and source row remains | `.omo/evidence/phase4/RP-08.json` |
| RP-09 | `uv run --package monitor-api pytest apps/api/tests/integration/test_retention.py::test_shared_tombstone_lifecycle` | tombstone survives first expiry and disappears only after last reference | `.omo/evidence/phase4/RP-09.json` |
| RP-10 | `uv run --package monitor-api pytest apps/api/tests/integration/test_daily_reports.py::test_late_pq_corrections_are_deterministic` | late analysis/match/engagement/source change corrects all in-window P/Q reports once | `.omo/evidence/phase4/RP-10.json` |

The focused CI invocations remain the approved ralplan commands. Their runner MUST additionally capture exit code, test-node ID, database assertions/query log, and redacted response bodies in each artifact. Exit code zero without the scenario-specific observable or a non-empty artifact is failure.

## 4. Critic pass-5 reconciliation

| Pass-5 finding | Contract resolution | Closure test | Decision |
|---|---|---|---|
| Circular run finalization and no enforced terminal marker | `complete` now invokes one atomic transition service; success requires the latest persisted contiguous terminal PageCommit, bound to cursor/ordinal/result chain. Skip/failure/zero-page/crash paths are separately guarded. | PC-01 through PC-11 | **Closed at contract level** |
| Hash-only report inputs cannot reproduce deleted semantic values | The 180-day canonical manifest now retains every formula-effective value and explicit null, both P/Q roles, effective mappings, engagement, and source status/coverage/timestamps. Tombstones gate deletion; replay reads only the payload and is query-guarded. | RP-01 through RP-10 | **Closed at contract level** |

The Critic pass-5 `ITERATE` verdict is therefore reconciled to **CLOSED for these two Phase 0 architecture findings**: neither implementation choice is left to an executor, and each invariant has a binary test. This does not claim product implementation, live source authorization, free-tier feasibility, Windows Codex capability, benchmark success, 30-day freshness, production approval, or consensus on evidence not yet gathered. Those existing fail-closed gates remain unchanged.

Product implementation MAY begin only after this document is accepted as the Phase 0 contract and the other Phase 0 external gates are handled under the approved ralplan. Implementations that weaken a MUST here require a new architecture review; a test or comment cannot waive the contract.
