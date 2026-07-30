"""Typed values and file boundaries for source-binding commands."""

# ruff: noqa: D101, D102, D103, D107, EM101, EM102, TRY003

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol, final, override
from uuid import UUID

from pydantic import JsonValue, TypeAdapter

JsonDocument = dict[str, JsonValue]
DOCUMENT: Final = TypeAdapter(JsonDocument)
BINDINGS: Final = TypeAdapter(list[JsonDocument])
OPTIONAL_UUID: Final[TypeAdapter[UUID | None]] = TypeAdapter(UUID | None)
OPTIONAL_STR: Final[TypeAdapter[str | None]] = TypeAdapter(str | None)
REPOSITORY: Final = "63amg0010-cpu/prediction-market-monitor"
TARGET_ARGS: Final = ("--repo", REPOSITORY, "--env", "production-collector")
MANIFOLD_SOURCE_ID: Final = UUID("0890756a-ca23-5697-ae4c-0de527361064")
ADVISORY_LOCK_SQL: Final = (
    "SELECT pg_advisory_lock(hashtext('production-collector-binding'))"
)
ADVISORY_UNLOCK_SQL: Final = (
    "SELECT pg_advisory_unlock(hashtext('production-collector-binding'))"
)
PLATFORMS: Final = frozenset({"dcinside", "manifold"})
MUTATING: Final = frozenset(
    {"apply-github", "handshake-github", "finalize-github", "restore-github"}
)
TransitionState = Literal[
    "binding_writing",
    "binding_committed",
    "handshake_passed",
    "github_finalized",
    "deactivated",
    "restore_writing",
]
Command = Literal[
    "capture-prestate",
    "render",
    "validate",
    "apply-github",
    "handshake-github",
    "finalize-github",
    "restore-github",
    "verify-github",
]
MutationCommand = Literal[
    "apply-github",
    "handshake-github",
    "finalize-github",
    "restore-github",
]
MUTATION_COMMAND: Final[TypeAdapter[MutationCommand]] = TypeAdapter(MutationCommand)


class CliError(RuntimeError):
    """Closed CLI failure rendered without a traceback."""


class BindingConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "different binding intent is already in flight"


@dataclass(frozen=True, slots=True)
class GitHubCommand:
    argv: tuple[str, ...]
    stdin: str | None = None


@dataclass(frozen=True, slots=True)
class BindingPayload:
    protected_json: str
    source_ids: str
    scope_version: str

    @property
    def sha256(self) -> str:
        value: JsonDocument = {
            "protected_json": self.protected_json,
            "scope_version": self.scope_version,
            "source_ids": self.source_ids,
        }
        return sha(value)


class GitHub(Protocol):
    def execute(self, command: GitHubCommand) -> str: ...


@final
class Args(argparse.Namespace):
    def __init__(self) -> None:
        super().__init__()
        self.command: Command = "render"
        self.activation_nonce: str = ""
        self.database_url_env: str | None = None
        self.predecessor_receipt: str | None = None
        self.binding_file: str | None = None
        self.payload_receipt: str | None = None
        self.prestate_receipt: str | None = None
        self.protected_json_file: str | None = None
        self.attestation_id: str | None = None
        self.cadence_anchor_at: str | None = None
        self.dispatch_nonce: str | None = None
        self.attempt: int | None = None
        self.collection_receipt: str | None = None
        self.handshake_receipt: str | None = None
        self.platform: list[str] | None = None
        self.json_out: str = ""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    _ = result.add_argument(
        "command",
        choices=(
            "capture-prestate",
            "render",
            "validate",
            "apply-github",
            "handshake-github",
            "finalize-github",
            "restore-github",
            "verify-github",
        ),
    )
    _ = result.add_argument("--activation-nonce", required=True)
    _ = result.add_argument("--database-url-env")
    _ = result.add_argument("--predecessor-receipt")
    _ = result.add_argument("--binding-file")
    _ = result.add_argument("--payload-receipt")
    _ = result.add_argument("--prestate-receipt")
    _ = result.add_argument("--protected-json-file")
    _ = result.add_argument("--attestation-id")
    _ = result.add_argument("--cadence-anchor-at")
    _ = result.add_argument("--dispatch-nonce")
    _ = result.add_argument("--attempt", type=int)
    _ = result.add_argument("--collection-receipt")
    _ = result.add_argument("--handshake-receipt")
    _ = result.add_argument("--platform", action="append")
    _ = result.add_argument("--json-out", required=True)
    return result


def required(value: str | None, flag: str) -> str:
    if not value:
        raise CliError(f"{flag} is required")
    return value


def load(path: str) -> JsonDocument:
    return DOCUMENT.validate_json(Path(path).read_bytes())


def canonical(value: JsonValue) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def sha(value: JsonValue) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def write(args: Args, receipt: JsonDocument) -> None:
    path = Path(args.json_out)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(canonical(receipt) + b"\n")


def field(document: JsonDocument, name: str) -> str:
    value = document.get(name)
    if not isinstance(value, str) or not value:
        raise CliError(f"{name} is missing")
    return value


def platforms(args: Args) -> list[str]:
    values = args.platform or []
    if len(values) != len(set(values)) or not values:
        raise CliError("--platform must contain a unique non-empty set")
    if not set(values).issubset(PLATFORMS):
        raise CliError("unsupported platform")
    return values


def binding_payload(document: JsonDocument, nonce: UUID) -> BindingPayload:
    if field(document, "activation_nonce") != str(nonce):
        raise CliError("activation nonce does not match receipt")
    payload = BindingPayload(
        protected_json=field(document, "protected_json"),
        source_ids=field(document, "source_ids"),
        scope_version=field(document, "scope_version"),
    )
    expected = document.get("payload_sha256")
    if expected is not None and expected != payload.sha256:
        raise CliError("payload hash does not match receipt")
    return payload
