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
