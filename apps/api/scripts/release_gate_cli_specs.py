"""Declarative argv contract for the unified fresh-search release gate."""

# ruff: noqa: RUF005

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

Action = Literal["value", "append", "one_or_more", "flag", "integer"]


@dataclass(frozen=True, slots=True)
class Option:
    """One stable argparse option."""

    name: str
    action: Action = "value"
    required: bool = True
    choices: tuple[str, ...] = ()
    dest: str | None = None


def _values(*names: str) -> tuple[Option, ...]:
    return tuple(Option(name) for name in names)


def _optional(*names: str) -> tuple[Option, ...]:
    return tuple(Option(name, required=False) for name in names)


def _appends(*names: str) -> tuple[Option, ...]:
    return tuple(Option(name, action="append") for name in names)


BINDINGS: Final = _values(
    "expected-sha",
    "expected-plan-sha256",
    "activation-nonce",
    "predecessor-receipt",
)
DB_BINDINGS: Final = _values("database-url-env") + BINDINGS
OUTPUT: Final = _values("json-out")
VERCEL_IDENTITY: Final = _values(
    "team-slug",
    "org-id-env",
    "project-name",
    "project-id-env",
    "token-env",
)

COMMANDS: Final = (
    "local-db",
    "code-quality",
    "no-spend-preflight",
    "bootstrap-dispatch",
    "bootstrap-select",
    "bootstrap-verify",
    "attest",
    "attestation-secret-upload",
    "canonical-hash",
    "evidence-import",
    "evidence-join",
    "recover-operation-receipt",
    "dispatch-reserve",
    "dispatch-workflow",
    "select-run",
    "verify-receipt",
    "materialize-chain",
    "activate",
    "vercel-prestate",
    "vercel-deploy",
    "vercel-restore",
    "rollback-finalize",
    "compat-state",
    "matrix-b-health",
    "production",
    "cadence",
    "acceptance-input-manifest",
    "acceptance-capture",
    "acceptance-refresh",
    "privacy-contain",
    "privacy-purge",
    "privacy-verify",
    "final-lane",
    "final-fan-in",
    "aggregate",
    "secret-static-scan",
    "plan-compliance",
    "scope-fidelity",
    "links",
)

SPECS: Final[dict[str, tuple[Option, ...]]] = {
    "local-db": (
        Option("phase", choices=("reprovision", "upgrade", "verify", "dispose")),
        *_values("database-url-env", "json-out"),
        *_optional(
            "expected-database", "admin-database-url-env", "required-start",
            "target", "guard-file", "expected-head", "expected-current",
            "expected-index",
        ),
    ),
    "code-quality": _values("base-sha", "reviewed-sha", "evidence-dir", "output"),
    "no-spend-preflight": _values(
        "review-record", "deployment-prestate",
        "production-measurements", "free-tier-result", "predecessor-receipt",
        "expected-sha", "expected-plan-sha256", "activation-nonce", "json-out",
    ) + _appends("provider-capture"),
    "bootstrap-dispatch": _values(
        "repository", "workflow", "display-title", "review-record",
        "deployment-prestate", "no-spend-receipt", "expected-sha",
        "expected-plan-sha256", "activation-nonce", "dispatch-nonce", "json-out",
    ) + (Option("attempt", "integer"),) + _optional("failed-attempt-receipt"),
    "bootstrap-select": _values(
        "repository", "workflow", "display-title", "dispatch", "expected-sha",
        "expected-plan-sha256", "activation-nonce", "dispatch-nonce", "json-out",
    ) + (Option("attempt", "integer"),),
    "bootstrap-verify": _values(
        "database-url-env", "review-record", "deployment-prestate",
        "no-spend-receipt", "dispatch", "selection", "operation", "expected-sha",
        "expected-plan-sha256", "activation-nonce", "dispatch-nonce", "json-out",
    ) + (Option("attempt", "integer"),),
    "attest": DB_BINDINGS + _values(
        "authorization-live-proof", "free-tier-result", "measurement-receipt",
        "attestation-out", "json-out",
    ) + _appends("provider-capture", "public-evidence-url") + (
        Option("attestation-generation", "integer"),
    ),
    "attestation-secret-upload": DB_BINDINGS + _values("attestation", "json-out"),
    "canonical-hash": _values("input", "json-out"),
    "evidence-import": _values(
        "kind", "input", "expected-input-sha256", "expected-sha",
        "expected-plan-sha256", "activation-nonce", "predecessor-receipt",
        "json-out",
    ),
    "evidence-join": _values(
        "deployment-root", "expected-branches", "expected-sha",
        "expected-plan-sha256", "activation-nonce", "predecessor-receipt",
        "json-out",
    ) + _appends("branch"),
    "recover-operation-receipt": _values(
        "database-url-env", "repository", "github-token-env", "workflow",
        "original-run-id", "operation", "revision", "dispatch-nonce",
        "activation-nonce", "expected-head-sha", "expected-plan-sha256",
        "expected-ledger-receipt-sha256", "json-out",
    ) + (Option("attempt", "integer"),),
    "dispatch-reserve": _values(
        "database-url-env", "repository", "workflow", "display-title", "head-sha",
        "expected-plan-sha256", "activation-nonce", "dispatch-nonce",
        "predecessor-receipt", "json-out",
    ) + (Option("attempt", "integer"),) + (
        Option("ref", required=False, dest="git_ref"),
    ),
    "dispatch-workflow": DB_BINDINGS + _values(
        "repository", "reservation", "workflow-spec", "base", "dispatch-nonce",
        "json-out",
    ) + (Option("attempt", "integer"),),
    "select-run": DB_BINDINGS + _values(
        "repository", "workflow", "display-title", "reservation",
        "dispatch-nonce", "json-out",
    ) + (Option("attempt", "integer"),) + _optional("github-token-env"),
    "verify-receipt": DB_BINDINGS + _values(
        "receipt", "selection", "reservation", "expected-command",
        "dispatch-nonce", "json-out",
    ) + (Option("attempt", "integer"),),
    "materialize-chain": DB_BINDINGS + _values(
        "manifest", "receipt-root", "expected-terminal-command", "json-out",
    ),
    "activate": (
        Option("phase", choices=("reserve", "commit", "reprepare", "restore")),
        *_values("database-url-env", "activation-nonce", "expected-sha", "json-out"),
        *_optional(
            "expected-plan-sha256", "attestation", "free-tier-result",
            "binding-handshake-receipt", "activation-reserve-receipt",
            "binding-finalize-receipt", "failed-reservation-receipt",
            "previous-attestation-receipt", "activation-evidence-receipt",
            "binding-restore-receipt", "restore-verification-receipt",
        ),
        Option("attestation-generation", "integer", required=False),
    ),
}
