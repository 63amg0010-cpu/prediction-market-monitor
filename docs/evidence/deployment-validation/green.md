# Public deployment validation

Date: 2026-07-24

This artifact records the secret-free validation completed before publishing the
deployment sources. It contains no credentials, service identifiers, hostnames,
local filesystem paths, or personal data.

## Green checks

- API contract and unit suite: 228 passed.
- Deployment and workflow contract subset: 16 passed.
- Python lint: passed.
- Python static types: 0 errors, 0 warnings, 0 notes.
- Web tests: 19 files and 77 tests passed.
- Generated API contract drift check: passed.
- Web TypeScript check: passed.
- Web lint: exited successfully with two existing specificity warnings.
- Next production build: passed and generated 14 routes.
- Gitleaks 8.30.1 final public-candidate scan: 0 findings.

## Boundaries

- URL-gated PostgreSQL proofs require an explicitly supplied database URL and are
  not represented as a live production result here.
- Live deployment credentials and provider credentials are never part of this
  artifact.
- Long-running production acceptance criteria remain governed by the operator
  procedure and are not inferred from these static checks.

## 2026-07-29 Todo11 workflow contract delta

The attempt-indexed `ci.yml`, `collect.yml`, and `verify.yml` workflow change was
validated without credentials:

- Todo11 plus existing workflow contract tests: 12 passed.
- Focused cloud-handoff secret/environment contract: passed.
- Dedicated Todo11 workflow contract lint: passed.
- Dedicated Todo11 workflow contract static types: 0 errors, 0 warnings, 0 notes.

The workflow claim request uses only the reviewed identity, plan, nonce,
reservation, GitHub run, ref, and Environment bindings. CI receives no
Production database credential. The protected migration inventory contains the
encrypted-backup identity and dump credential, but no restore credential because
the migration workflow does not perform automatic restore.

The older broad green-check counts above remain a dated baseline. This delta does
not claim that those historical counts were rerun against the current tree, nor
does it represent a live Production or PostgreSQL result.
