"""Closed contracts shared by the release Vercel command handlers."""

# ruff: noqa: D101, D102, D103, EM101, TC003

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

TEAM_SLUG = "63amg0010-5358-projects"
CLI_VERSION = "51.7.0"
PROJECTS = {
    "api": ("prediction-monitor-api", "VERCEL_API_PROJECT_ID", "apps/api"),
    "web": ("prediction-monitor-web", "VERCEL_WEB_PROJECT_ID", "apps/web"),
}
ORG_ID_ENV = "VERCEL_ORG_ID"
TOKEN_ENV = "VERCEL_TOKEN"  # noqa: S105 - environment variable name, not a secret.


class ReleaseHoldError(ValueError):
    """The immutable release contract was not satisfied."""


@dataclass(frozen=True, slots=True)
class ChildCommand:
    """One auditable child invocation with secrets passed only by env name."""

    stage: str
    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ChildResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class ChildRunner(Protocol):
    def execute(self, command: ChildCommand) -> ChildResult: ...


@dataclass(frozen=True, slots=True)
class VercelOperation:
    operation: Literal[
        "initial-deploy",
        "compat-alias",
        "split-compensation",
        "matrix-b-rebuild",
    ]
    attempt: Literal[1, 2]
    attempt_root: Path
    repository_root: Path
    project_kind: Literal["api", "web"]
    team_slug: str
    org_id_env: str
    project_name: str
    project_id_env: str
    token_env: str
    target_sha: str
    protected_ref: str
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: UUID
    cli_version: str
    predecessor_receipt: Mapping[str, object]
    deployment_prestate: Mapping[str, object] | None = None
    target_deployment_receipt: Mapping[str, object] | None = None
    environment: str = "production"

    @property
    def alias(self) -> str:
        if self.operation == "compat-alias":
            return f"{self.project_name}-fresh-search-compat.vercel.app"
        return f"{self.project_name}.vercel.app"

    @property
    def project_root(self) -> str:
        return PROJECTS[self.project_kind][2]


@dataclass(frozen=True, slots=True)
class VercelPrestateRequest:
    repository_root: Path
    project_kind: Literal["api", "web"]
    team_slug: str
    org_id_env: str
    project_name: str
    project_id_env: str
    token_env: str
    protected_ref: str
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: UUID
    cli_version: str

    @property
    def alias(self) -> str:
        return f"{self.project_name}.vercel.app"


def canonical_bytes(value: Mapping[str, object]) -> bytes:
    """Emit the JSON subset used by receipts in deterministic RFC-8785 order."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def receipt_sha256(value: Mapping[str, object]) -> str:
    body = {key: item for key, item in value.items() if key != "receipt_sha256"}
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def seal_receipt(value: Mapping[str, object]) -> dict[str, object]:
    result = dict(value)
    result["receipt_sha256"] = receipt_sha256(result)
    return result


def verify_receipt(
    receipt: Mapping[str, object],
    *,
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: UUID,
) -> str:
    if receipt.get("receipt_sha256") != receipt_sha256(receipt):
        raise ReleaseHoldError("receipt_hash_mismatch")
    if receipt.get("reviewed_sha") != expected_sha:
        raise ReleaseHoldError("wrong_reviewed_sha")
    if receipt.get("approved_plan_sha256") != expected_plan_sha256:
        raise ReleaseHoldError("wrong_plan_sha")
    if receipt.get("activation_nonce") != str(activation_nonce):
        raise ReleaseHoldError("wrong_activation_nonce")
    if receipt.get("accepted") is not True:
        raise ReleaseHoldError("predecessor_not_accepted")
    digest = receipt.get("receipt_sha256")
    if not isinstance(digest, str):
        raise ReleaseHoldError("missing_receipt_hash")
    return digest


def verify_failed_receipt(
    receipt: Mapping[str, object],
    *,
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: UUID,
) -> str:
    if receipt.get("receipt_sha256") != receipt_sha256(receipt):
        raise ReleaseHoldError("receipt_hash_mismatch")
    if receipt.get("reviewed_sha") != expected_sha:
        raise ReleaseHoldError("wrong_reviewed_sha")
    if receipt.get("approved_plan_sha256") != expected_plan_sha256:
        raise ReleaseHoldError("wrong_plan_sha")
    if receipt.get("activation_nonce") != str(activation_nonce):
        raise ReleaseHoldError("wrong_activation_nonce")
    digest = receipt.get("receipt_sha256")
    if not isinstance(digest, str):
        raise ReleaseHoldError("missing_receipt_hash")
    return digest


__all__ = (
    "CLI_VERSION",
    "ORG_ID_ENV",
    "PROJECTS",
    "TEAM_SLUG",
    "TOKEN_ENV",
    "ChildCommand",
    "ChildResult",
    "ChildRunner",
    "ReleaseHoldError",
    "VercelOperation",
    "VercelPrestateRequest",
    "receipt_sha256",
    "seal_receipt",
    "verify_failed_receipt",
    "verify_receipt",
)
