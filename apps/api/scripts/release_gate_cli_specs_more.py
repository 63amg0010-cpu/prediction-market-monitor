"""Remaining unified release-gate argv declarations."""

# ruff: noqa: RUF005

from __future__ import annotations

from typing import Final

from scripts.release_gate_cli_specs import (
    DB_BINDINGS,
    Option,
)


def _v(*names: str) -> tuple[Option, ...]:
    return tuple(Option(name) for name in names)


def _o(*names: str) -> tuple[Option, ...]:
    return tuple(Option(name, required=False) for name in names)


def _a(*names: str) -> tuple[Option, ...]:
    return tuple(Option(name, action="append") for name in names)


MORE_SPECS: Final[dict[str, tuple[Option, ...]]] = {
    "vercel-prestate": _v(
        "database-url-env", "plan", "review-record", "expected-sha", "team-slug",
        "org-id-env", "api-project-name", "api-project-id-env",
        "web-project-name", "web-project-id-env", "token-env",
        "activation-nonce", "predecessor-receipt", "json-out",
    ),
    "vercel-deploy": DB_BINDINGS + _v(
        "operation", "attempt-root", "project-kind", "team-slug", "org-id-env",
        "project-name", "project-id-env", "token-env", "protected-ref",
        "cli-version", "json-out",
    ) + (Option("attempt", "integer"),) + _o(
        "target-sha", "target-deployment-receipt",
    ),
    "vercel-restore": DB_BINDINGS + _v(
        "operation", "attempt-root", "project-kind", "team-slug", "org-id-env",
        "project-name", "project-id-env", "token-env", "target-sha",
        "protected-ref", "cli-version", "json-out",
    ) + (Option("attempt", "integer"),) + _o("deployment-prestate"),
    "rollback-finalize": DB_BINDINGS + _v(
        "incident-class", "matrix-b-chain", "json-out",
    ),
    "compat-state": DB_BINDINGS + _v(
        "api-url", "web-url", "api-receipt", "web-receipt",
        "api-alias-receipt", "web-alias-receipt", "cadence-anchor-at", "json-out",
    ),
    "matrix-b-health": DB_BINDINGS + _v(
        "api-url", "web-url", "downgrade-receipt", "binding-restore-receipt",
        "api-receipt", "web-receipt", "expected-current", "json-out",
    ) + (Option("read-only", "flag"),),
    "production": DB_BINDINGS + _v(
        "api-url", "web-url", "expected-revision", "attestation",
        "free-tier-result", "release-chain", "json-out",
    ) + (Option("read-only", "flag", required=False),),
    "cadence": (
        Option("phase", choices=("initial", "status", "acceptance")),
        *DB_BINDINGS,
        *_v("epoch-id", "json-out"),
        *_a("source-id"),
        *_o("prior-cadence", "activation-chain"),
    ),
    "acceptance-input-manifest": DB_BINDINGS + _v(
        "authorization-evidence", "provider-manifest", "local-measurements",
        "production-measurements", "output-root", "json-out",
    ) + _a("provider-capture"),
    "acceptance-capture": DB_BINDINGS + _v(
        "repository", "github-repository-id-env", "team-slug", "org-id-env",
        "api-project-name", "api-project-id-env", "web-project-name",
        "web-project-id-env", "supabase-project-id-env", "supabase-org-id-env",
        "github-token-env", "vercel-token-env", "api-url", "web-url",
        "input-manifest", "authorization-evidence", "provider-manifest",
        "local-measurements", "production-measurements", "free-tier-result",
        "output-dir", "json-out",
    ) + _a("provider-capture"),
    "acceptance-refresh": DB_BINDINGS + _v(
        "api-url", "web-url", "input-manifest", "authorization-evidence",
        "provider-manifest", "local-measurements", "production-measurements",
        "free-tier-result", "current-receipt-dir", "current-state-manifest",
        "json-out",
    ) + _a("provider-capture") + (Option("expected-members", "integer"),),
    "privacy-contain": DB_BINDINGS + _v(
        "source-id", "epoch-id", "violation-kind", "json-out",
    ),
    "privacy-purge": DB_BINDINGS + _v(
        "repository", "github-token-env", "source-id", "epoch-id",
        "violation-kind", "containment-receipt", "json-out",
    ),
    "privacy-verify": DB_BINDINGS + _v(
        "api-url", "web-url", "repository", "github-repository-id-env",
        "github-token-env", "team-slug", "org-id-env", "api-project-name",
        "api-project-id-env", "web-project-name", "web-project-id-env",
        "vercel-token-env", "supabase-org-id-env", "supabase-project-id-env",
        "source-id", "epoch-id", "violation-kind", "containment-receipt",
        "purge-receipt", "matrix-b-health", "matrix-b-chain",
        "expected-current", "json-out",
    ),
    "final-lane": DB_BINDINGS + _v(
        "lane", "report", "production-result", "json-out",
    ) + _o("aux-report", "cadence"),
    "final-fan-in": DB_BINDINGS + _v(
        "parent", "expected-branches", "json-out",
    ) + _a("branch"),
    "aggregate": DB_BINDINGS + _v(
        "fan-in", "f4", "cadence", "json-out",
    ) + _o("acceptance-refresh"),
    "secret-static-scan": _v("root", "base-sha", "reviewed-sha", "json-out"),
    "plan-compliance": _o(
        "root", "json-out", "plan", "base-sha", "reviewed-sha", "evidence-dir",
        "production-result", "expected-revision", "output",
    ),
    "scope-fidelity": _o(
        "root", "json-out", "plan", "base-sha", "reviewed-sha", "evidence-dir",
        "production-result", "fan-in", "cadence", "acceptance-refresh",
        "expected-sha", "expected-plan-sha256", "activation-nonce",
        "predecessor-receipt", "output",
    ),
    "links": _v("root", "json-out") + (Option("paths", "one_or_more"),),
}
