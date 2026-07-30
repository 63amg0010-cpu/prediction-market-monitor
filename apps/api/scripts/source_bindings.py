"""Serialized, redacted GitHub source-binding state machine."""

# ruff: noqa: D101, D102, D107, E501, SIM905

from __future__ import annotations

import hashlib
import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Final, Literal, Protocol, override
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import Generator

REPOSITORY: Final = "63amg0010-cpu/prediction-market-monitor"
TARGET_ARGS: Final = ("--repo", REPOSITORY, "--env", "production-collector")
MANIFOLD_SOURCE_ID: Final = UUID("0890756a-ca23-5697-ae4c-0de527361064")
ADVISORY_LOCK_SQL: Final = (
    "SELECT pg_advisory_lock(hashtext('production-collector-binding'))"
)
INTENT_COLUMNS: Final = tuple("id activation_nonce source_id attestation_id payload_sha256 prestate_sha256 scope_version created_at_db".split())  # fmt: skip
COMMANDS: Final = tuple("capture-prestate render validate apply-github handshake-github finalize-github restore-github verify-github".split())  # fmt: skip
MUTATING_COMMANDS: Final = frozenset(
    "apply-github handshake-github finalize-github restore-github".split()
)
BindingState = Literal[
    "binding_writing",
    "binding_committed",
    "handshake_passed",
    "github_finalized",
    "deactivated",
    "restore_writing",
]


class BindingConflictError(RuntimeError):
    """Raised when a different nonce or payload attempts to resume an intent."""

    @override
    def __str__(self) -> str:
        return "different binding intent is already in flight"


@dataclass(frozen=True, slots=True)
class GitHubCommand:
    argv: tuple[str, ...]
    stdin: str | None = None


class GitHub(Protocol):
    def execute(self, command: GitHubCommand) -> str: ...


@dataclass(frozen=True, slots=True)
class BindingPayload:
    protected_json: str
    source_ids: str
    scope_version: str

    @property
    def sha256(self) -> str:
        canonical = json.dumps(
            {
                "scope_version": self.scope_version,
                "protected_json": self.protected_json,
                "source_ids": self.source_ids,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class BindingIntent:
    activation_nonce: UUID
    payload: BindingPayload
    payload_sha256: str

    @classmethod
    def create(
        cls,
        *,
        activation_nonce: UUID,
        payload: BindingPayload,
    ) -> BindingIntent:
        return cls(
            activation_nonce=activation_nonce,
            payload=payload,
            payload_sha256=payload.sha256,
        )


@dataclass(frozen=True, slots=True)
class BindingReceipt:
    activation_nonce: UUID
    payload_sha256: str
    state: BindingState
    recovered_after_lost_receipt: bool


class IntentJournal:
    """Mutable append-only testable journal behind the database lock boundary."""

    def __init__(self) -> None:
        self._lock: RLock = RLock()
        self._active: tuple[UUID, str] | None = None
        self.transitions: list[BindingState] = []

    @contextmanager
    def serialized(self, intent: BindingIntent) -> Generator[None]:
        with self._lock:
            identity = (intent.activation_nonce, intent.payload_sha256)
            if self._active is not None and self._active != identity:
                raise BindingConflictError
            self._active = identity
            yield

    def begin_write(self) -> bool:
        if self.transitions and self.transitions[-1] in {
            "binding_writing",
            "binding_committed",
        }:
            return False
        self.transitions.append("binding_writing")
        return True

    def append(self, state: BindingState) -> None:
        self.transitions.append(state)


class BindingStateMachine:
    def __init__(self, *, github: GitHub, journal: IntentJournal) -> None:
        self._github: GitHub = github
        self._journal: IntentJournal = journal

    def apply_github(self, intent: BindingIntent) -> BindingReceipt:
        with self._journal.serialized(intent):
            first_write = self._journal.begin_write()
            if not first_write and self._live_binding_matches(intent.payload):
                if self._journal.transitions[-1] != "binding_committed":
                    self._journal.append("binding_committed")
                return self._receipt(intent, recovered=True)
            self._set_secret(intent.payload.protected_json)
            self._set_variable("MONITOR_SOURCE_IDS", intent.payload.source_ids)
            self._set_variable("MONITOR_SCOPE_VERSION", intent.payload.scope_version)
            self._journal.append("binding_committed")
            return self._receipt(intent, recovered=False)

    def finalize_github(
        self,
        intent: BindingIntent,
        *,
        cadence_anchor_at: str,
    ) -> BindingReceipt:
        with self._journal.serialized(intent):
            self._set_variable(
                "MONITOR_DEPLOYMENT_ACTIVATION_AT",
                cadence_anchor_at,
            )
            self._journal.append("github_finalized")
            return self._receipt(intent, recovered=False)

    def restore_github(
        self,
        intent: BindingIntent,
        prestate: BindingPayload,
    ) -> BindingReceipt:
        with self._journal.serialized(intent):
            self._journal.append("deactivated")
            self._journal.append("restore_writing")
            self._set_secret(prestate.protected_json)
            self._set_variable("MONITOR_SOURCE_IDS", prestate.source_ids)
            self._set_variable("MONITOR_SCOPE_VERSION", prestate.scope_version)
            return BindingReceipt(
                activation_nonce=intent.activation_nonce,
                payload_sha256=prestate.sha256,
                state="restore_writing",
                recovered_after_lost_receipt=False,
            )

    def _live_binding_matches(self, payload: BindingPayload) -> bool:
        source_ids = self._get_variable("MONITOR_SOURCE_IDS")
        scope = self._get_variable("MONITOR_SCOPE_VERSION")
        return source_ids == payload.source_ids and scope == payload.scope_version

    def _set_secret(self, value: str) -> None:
        _ = self._github.execute(
            GitHubCommand(
                (
                    "gh",
                    "secret",
                    "set",
                    "MONITOR_SOURCE_BINDINGS_JSON",
                    *TARGET_ARGS,
                    "--body",
                    "-",
                ),
                value,
            )
        )

    def _set_variable(self, name: str, value: str) -> None:
        _ = self._github.execute(
            GitHubCommand(
                ("gh", "variable", "set", name, *TARGET_ARGS, "--body", "-"),
                value,
            )
        )

    def _get_variable(self, name: str) -> str:
        return self._github.execute(
            GitHubCommand(
                (
                    "gh",
                    "variable",
                    "get",
                    name,
                    *TARGET_ARGS,
                    "--json",
                    "value",
                    "--jq",
                    ".value",
                )
            )
        ).strip()

    @staticmethod
    def _receipt(intent: BindingIntent, *, recovered: bool) -> BindingReceipt:
        return BindingReceipt(
            intent.activation_nonce,
            intent.payload_sha256,
            state="binding_committed",
            recovered_after_lost_receipt=recovered,
        )


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.source_bindings_runtime import main

    raise SystemExit(main())
