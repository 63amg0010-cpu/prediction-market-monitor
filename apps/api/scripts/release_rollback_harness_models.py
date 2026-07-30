"""Small typed contracts shared by the rollback harness adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypedDict

if TYPE_CHECKING:
    from pathlib import Path


class MatrixCommand(TypedDict):
    """One captured external command that is never executed."""

    argv: list[str]
    cwd: str
    stage: str


class DatabaseReceipt(TypedDict):
    """Closed database poststate projection."""

    dcinside_binding_sha256: str
    dcinside_preserved: bool
    manifold_enabled: bool
    manifold_pointers_null: bool
    name: str
    revision_after: str
    revision_before: str
    revision_peak: str


class ExternalReceipt(TypedDict):
    """Closed proof that only stub argv was rendered."""

    commands: list[MatrixCommand]
    executed_count: int
    mode: str
    network_access: bool
    production_access: bool


class ExternalRecorder(Protocol):
    """Injected capture-only boundary for forbidden provider commands."""

    def record(self, commands: list[MatrixCommand]) -> ExternalReceipt:
        """Record redacted argv without executing a child."""
        ...


class HarnessReceipt(TypedDict):
    """Closed public receipt emitted by a successful rehearsal."""

    accepted: bool
    database: DatabaseReceipt
    external: ExternalReceipt
    mode: str
    reviewed_sha: str
    schema: str


@dataclass(frozen=True, slots=True)
class Options:
    """Validated command-line values for one disposable rehearsal."""

    mode: str
    database_url_env: str
    stub_external: bool
    expected_sha: str
    json_out: Path


@dataclass(frozen=True, slots=True)
class DatabaseSnapshot:
    """Redacted database facts required at a migration boundary."""

    revision: str
    manifold_present: bool
    manifold_enabled: bool
    manifold_pointers_null: bool
    dcinside_binding_sha256: str


class Database(Protocol):
    """Injected migration/snapshot boundary."""

    def snapshot(self, url: str) -> DatabaseSnapshot:
        """Read the schema-closed release poststate."""
        ...

    def migrate(self, url: str, direction: str, revision: str) -> None:
        """Move to one exact Alembic revision."""
        ...


__all__ = (
    "Database",
    "DatabaseReceipt",
    "DatabaseSnapshot",
    "ExternalReceipt",
    "ExternalRecorder",
    "HarnessReceipt",
    "MatrixCommand",
    "Options",
)
