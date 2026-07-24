"""Typed service principals and authorization scopes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import NewType

PrincipalId = NewType("PrincipalId", str)
CredentialVersion = NewType("CredentialVersion", str)


@unique
class PrincipalKind(StrEnum):
    """Closed set of identities allowed to call the API."""

    BFF = "bff"
    GITHUB_COLLECTOR = "github_collector"
    GITHUB_VERIFIER = "github_verifier"
    WINDOWS_WORKER = "windows_worker"
    ADMIN_SESSION = "admin_session"


@unique
class Scope(StrEnum):
    """Closed set of service-token capabilities."""

    BFF_AUTH = "bff:auth"
    BFF_READ = "bff:read"
    ADMIN_COMMAND = "admin:command"
    COLLECTOR_MATERIALIZE = "collector:materialize"
    COLLECTOR_RESERVE = "collector:reserve"
    COLLECTOR_CLAIM = "collector:claim"
    COLLECTOR_PAGE_COMMIT = "collector:page_commit"
    COLLECTOR_HEARTBEAT = "collector:heartbeat"
    COLLECTOR_COMPLETE = "collector:complete"
    VERIFY_READ = "verify:read"
    VERIFY_WRITE = "verify:write"
    WORKER_LEASE = "worker:lease"
    WORKER_HEARTBEAT = "worker:heartbeat"
    WORKER_ACK = "worker:ack"


@dataclass(frozen=True, slots=True)
class Principal:
    """A versioned non-browser service identity."""

    id: PrincipalId
    kind: PrincipalKind
    credential_version: CredentialVersion
