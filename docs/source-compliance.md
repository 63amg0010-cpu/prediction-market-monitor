# Source compliance

Status: **fail-closed source activation guide**

Public pages are not automatically collectable. A source can be enabled only when official evidence, exact route, permitted fields, purpose, rate, concurrency, reviewer, and expiry/recheck date are recorded. No paid fallback and no login bypass are allowed.

Manifold deployment, activation, rollback, and privacy response additionally follow [Manifold staged release operations](manifold-release-operations.md). The sole Alembic head `20260727_0011` prepares append-only evidence while leaving Manifold disabled and unlinked; migration success is not source activation.

## Current source state

The active config is `config/sources.reviewed.yml`.

| Source | Current decision | Reason |
|---|---|---|
| Reddit | `pending_evidence`, `enabled: false` | OAuth Data API registration/approval, token scope, exact limits, and observed route evidence are not yet recorded. |
| DCInside | `approved`, `enabled: true` | Exact `predictionmarket` mini-gallery list/view routes, retained fields, 30 RPM, concurrency 1, reviewer, and 2026-08-25 recheck expiry are recorded. |
| Manifold | `approved`, `enabled: false` | Official API use is reviewed for the exact market/comment GET routes, personal noncommercial use, 30 RPM, concurrency 1, at most 20 accepted comments/run, and a minimized field projection. Activation stays closed through `0011` and opens only after fresh evidence, no-spend, binding handshake/finalize, and activation commit receipts pass. |
| Toss | `pending_evidence`, `enabled: false` | No written or official community-data authorization has been recorded. |
| Naver Finance | `pending_evidence`, `enabled: false` | Current reviewed evidence blocks community-board crawling; no approved API/client route is recorded. |

Toss and Naver are mutually exclusive. At most one may be enabled after its own authorization is approved. Reddit remains blocked until an approved OAuth Data API grant is supplied; DCInside is the only currently enabled source.

## Activation checklist

Before changing any source to enabled:

1. Recheck official terms, robots policy, developer/API documentation, account dashboard limits, and route availability.
2. Record immutable evidence in `docs/evidence/source-scope-register.md` or a replacement evidence record.
3. Record exact allowed route/method/field/purpose/rate/concurrency.
4. Confirm the adapter strips author/profile/raw provider payload before persistence and logs.
5. Confirm every applicable current/projected free-tier dimension is known and strictly below 70%, with paid/overage/add-on paths disabled.
6. Confirm tests cover authorization inactive, revoked, quota skip, route mismatch, and author/raw rejection.

If any item is missing, leave the source disabled and show the dashboard state as blocked/pending, not zero activity. Manifold additionally requires `docs/evidence/manifold-authorization.json` plus a fresh database-time `manifold_evidence.py refresh` receipt; a changed scope, stale proof, failed neutral-link identity check, incomplete projection, unknown quota, or missing predecessor is `HOLD`.

Before activation commit, the only permitted Manifold requests are the reviewed read-only authorization `probe`/`refresh` calls. They store no provider body or structured identity, use no collector, and do not count as collection or cadence. Binding-prestate and binding-handshake modes must make zero provider requests. Real smoke collection is allowed only after activation commit.

## Data that may be retained

Allowed for accepted posts:

- source-local post ID
- canonical source URL
- title and body, up to the 256 KiB accepted limit
- publication timestamp
- language
- comments count and score/upvote count when available
- content hash and version metadata

For Manifold, the retained provider projection is narrower: market ID/question/slug/neutral public link plus comment ID/market ID/publication time/plain-text comment. The adapter must discard structured creator/author name or ID, username, profile URL, avatar, wallet/address fields, creator URL segments, and the raw response before persistence or logging.

Not allowed as structured provider data:

- author name, profile URL, avatar, user ID, flair, or account metadata
- raw provider payload
- private, login-gated, or bypassed content
- data from routes outside the reviewed source record

“Author-free” does not claim heuristic redaction of arbitrary identity-like words that a user typed into a public title/body. Such literal public text remains searchable content inside the approved scope. Authorization revocation or a privacy incident purges the complete affected rows and derived artifacts rather than pretending those words were sanitized.

Oversize posts are rejected as a whole and retain only source ID, URL, content hash, size, and reason.

## Freshness evidence rule

The three-hour freshness requirement is not satisfied by a schedule file. One cadence epoch freezes the exact source set `{DCInside, Manifold}` and the half-open interval `anchor <= t < anchor + 30d`. It contains exactly 240 workflow-level three-hour collection slots and 2,880 workflow-level fifteen-minute verifier slots. These counts are not multiplied by source count; each accepted slot contains successful subreceipts for both sources. It requires evidence with:

- every expected 15-minute verifier slot observed, with zero missing verifier slots;
- scheduler latency, collection recency, and publication latency recorded;
- every frozen source/run staying within the slot's exact start/completion bounds;
- no missing, unauthorized, quota-blocked, or failed source window counted as pass.
- no smoke/manual handshake run counted as scheduled cadence.

Evidence procedure:

1. Before `anchor-1h`, commit activation and freeze the epoch, two-source set, scope/binding hashes, due instants, reviewed commit, and fresh public-repository evidence.
2. Materialize all 240/2,880 slots in advance. Collection due keys are UTC minute 17 every three hours; verifier keys are quarter-hour UTC.
3. Accept exactly one timely successful attempt per slot by CAS. A failure followed by a retry may pass only when that retry starts/completes inside the original slot window. Late, wrong-scope, partial-source, duplicate-only, or missing attempts fail.
4. Keep day-zero status at `OPERATIONAL_PENDING_CADENCE` / `cadence_30d=HOLD`. Smoke, manual, local, CI, or GitHub run history cannot turn it into complete.
5. After the 30-day window closes, create fresh `<2h` authorization/provider/Production captures, the exact eight-leaf input set and ninth free-tier result, and the fifteen-member current-state manifest through `acceptance-input-manifest`, `acceptance-capture`, and `acceptance-refresh`.
6. Run `cadence --phase acceptance`, scope-fidelity, final-lane, and aggregate. Only exact current-state membership plus durable 240/2,880 evidence may emit `COMPLETE`.

Until this procedure passes, production acceptance is blocked even if local tests, workflow contract tests, day-zero deployment, or real smoke runs pass. The requirement is the exact half-open 30-day epoch above.
