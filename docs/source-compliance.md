# Source compliance

Status: **fail-closed source activation guide**

Public pages are not automatically collectable. A source can be enabled only when official evidence, exact route, permitted fields, purpose, rate, concurrency, reviewer, and expiry/recheck date are recorded. No paid fallback and no login bypass are allowed.

## Current source state

The active config is `config/sources.reviewed.yml`.

| Source | Current decision | Reason |
|---|---|---|
| Reddit | `pending_evidence`, `enabled: false` | OAuth Data API registration/approval, token scope, exact limits, and observed route evidence are not yet recorded. |
| DCInside | `pending_evidence`, `enabled: false` | Generic robots allowance is not enough for monitoring approval; exact reviewed route is missing. |
| Toss | `pending_evidence`, `enabled: false` | No written or official community-data authorization has been recorded. |
| Naver Finance | `pending_evidence`, `enabled: false` | Current reviewed evidence blocks community-board crawling; no approved API/client route is recorded. |

Toss and Naver are mutually exclusive. At most one may be enabled after its own authorization is approved.

## Activation checklist

Before changing any source to enabled:

1. Recheck official terms, robots policy, developer/API documentation, account dashboard limits, and route availability.
2. Record immutable evidence in `docs/evidence/source-scope-register.md` or a replacement evidence record.
3. Record exact allowed route/method/field/purpose/rate/concurrency.
4. Confirm the adapter strips author/profile/raw provider payload before persistence and logs.
5. Confirm free quota soft stop at 70% and hard stop at 80%.
6. Confirm tests cover authorization inactive, revoked, quota skip, route mismatch, and author/raw rejection.

If any item is missing, leave the source disabled and show the dashboard state as blocked/pending, not zero activity.

## Data that may be retained

Allowed for accepted posts:

- source-local post ID
- canonical source URL
- title and body, up to the 256 KiB accepted limit
- publication timestamp
- language
- comments count and score/upvote count when available
- content hash and version metadata

Not allowed:

- author name, profile URL, avatar, user ID, flair, or account metadata
- raw provider payload
- private, login-gated, or bypassed content
- data from routes outside the reviewed source record

Oversize posts are rejected as a whole and retain only source ID, URL, content hash, size, and reason.

## Freshness evidence rule

The three-hour freshness requirement is not satisfied by a schedule file. One acceptance window is 30 complete consecutive UTC days and contains exactly 240 expected three-hour collection slots and 2,880 expected fifteen-minute verifier slots. It requires evidence with:

- every expected 15-minute verifier slot observed, with zero missing verifier slots;
- scheduler latency, collection recency, and publication latency recorded;
- every enabled source/run staying within the required three-hour bounds;
- no missing, unauthorized, quota-blocked, or failed source window counted as pass.

Evidence procedure:

1. Fix the UTC window boundaries, immutable scope version, default-branch workflow commit, and repository-visibility proof before counting begins.
2. Export the durable collection slots, verifier expected slots, observations, per-source S/C/P results, publication manifests, and linked GitHub run IDs for that exact window.
3. Require exactly 240 distinct expected collection slots and 2,880 distinct expected verifier slots. Duplicate workflow runs do not increase either count.
4. Require one retained passing observation for every verifier slot, zero missing verifier slots, and every enabled source/run condition to pass. A delayed or dropped GitHub schedule remains a failure even if a later run backfills data.
5. Hash the immutable evidence report, record reviewer and review time, and retain the report with its source row identifiers. GitHub run history alone is supporting evidence, not the acceptance record.

Until this procedure passes, production acceptance is blocked even if local tests or workflow contract tests pass.
