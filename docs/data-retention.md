# Data retention

Status: **implementation contract summary**

The detailed normative contract is `docs/architecture/phase0-execution-contracts.md`. This document is the operator-facing summary.

## Retention goals

- Keep accepted post title/body long enough to support collection, analysis, and report generation.
- Never store author/profile/raw provider payload.
- Preserve report formulas for 180 days without querying deleted source rows.
- Delete or tombstone old source rows in the right order so reports remain reproducible.

## Current policy

| Data | Retention | Notes |
|---|---|---|
| Accepted post title/body/version | 30 days before eligible cleanup | Cleanup is blocked if a retained report manifest still needs an unswitched live reference. |
| Oversize rejected item | hash, URL, size, reason only | Full oversize body is not retained. |
| Engagement observation | value-bearing dependency until report manifest/tombstone rules allow cleanup | Null means unavailable, never zero. |
| Analysis output | immutable per post-version/prompt/model/schema | Missing analysis is pending/blocked/failed, never neutral. |
| Daily report input manifest | 180 days | Stores every formula-effective value and explicit null. |
| Report input tombstone | until last referencing manifest expires | Contains IDs/hashes/source/date/reason, not original text or author. |
| Source publication and run evidence | retained as required for freshness and report provenance | Deletion must not break report reproduction. |

## Report reproduction rule

`reproduce_report(manifest_id)` must read only the retained manifest payload and stored report projection/hash. It must not query posts, post versions, analyses, matches, engagement observations, source runs, publication manifests, checkpoints, or source coverage source tables.

If payload length, hash, canonical order, formula values, explicit nulls, or projection bytes differ, the result is `manifest_corrupt`. It must not fall back to live tables.

## Cleanup order

Before deleting a source row referenced by a retained manifest:

1. Lock the source row and every referencing manifest/item.
2. Verify source hash, manifest payload hash, and item value-slice hash.
3. Prove the manifest already contains every required formula value.
4. Create or reuse a tombstone.
5. Switch the manifest item from live FK to tombstone FK.
6. Delete the source row only after the switch succeeds.

After manifest expiry, delete report pointers/dependencies first, then manifest items, manifest payload, and finally tombstones with zero remaining references.

## Operator warnings

- Do not manually delete database rows to save space.
- Do not run cleanup after a hash mismatch or unknown schema.
- Do not treat a stored report hash alone as reproduction proof.
- Do not store raw provider payload or author data in tombstones, logs, screenshots, or artifacts.
