"""Typed SQLAlchemy fakes for privacy runtime adapter tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast, final

from scripts.runtime_privacy_adapter_db import PrivacyDatabaseAdapter
from scripts.runtime_privacy_adapter_sql import (
    APPEND_DEACTIVATED,
    APPEND_RESTORED,
    DB_TIME,
    DC_FINGERPRINT,
    DISABLE,
    INVALIDATE,
    LATEST,
    LOCK,
    PURGE,
    SCOPE,
    TARGETS,
    VERIFY,
    ZERO,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from scripts.runtime_privacy_adapter import PrivacyProofSession
    from sqlalchemy.ext.asyncio import AsyncEngine

NOW = datetime(2026, 7, 29, 6, tzinfo=UTC)


@final
class Result:
    """Small result facade covering the adapter's used SQLAlchemy surface."""

    def __init__(
        self,
        value: object = None,
        *,
        rowcount: int = 1,
    ) -> None:
        self.value: object = value
        self.rowcount: int = rowcount

    def mappings(self) -> Result:
        return self

    def one_or_none(self) -> object:
        return self.value

    def one(self) -> object:
        return self.value

    def scalar_one(self) -> object:
        return self.value

    def __iter__(self) -> Iterator[object]:
        if not isinstance(self.value, tuple):
            raise TypeError
        return iter(cast("tuple[object, ...]", self.value))


@final
class FakeEngine:
    """Transaction state and scripted database observations."""

    def __init__(self, state: str) -> None:
        self.state: str = state
        self.fail: str = ""
        self.dc_after: tuple[int, str] = (8, "edge")
        self.calls: list[str] = []
        self.commits: int = 0
        self.rollbacks: int = 0
        self.begins: int = 0
        self.disposals: int = 0

    def begin(self) -> Transaction:
        self.begins += 1
        return Transaction(self)

    async def dispose(self) -> None:
        self.disposals += 1


@final
class Transaction:
    """Commit or roll back according to the callback outcome."""

    def __init__(self, owner: FakeEngine) -> None:
        self.owner: FakeEngine = owner

    async def __aenter__(self) -> Connection:
        return Connection(self.owner)

    async def __aexit__(
        self,
        error_type: object,
        error: object,
        traceback: object,
    ) -> None:
        del error_type, traceback
        if error is None:
            self.owner.commits += 1
        else:
            self.owner.rollbacks += 1


@final
class Connection:
    """Return deterministic rows keyed by exact SQL statement identity."""

    def __init__(self, owner: FakeEngine) -> None:
        self.owner: FakeEngine = owner
        self.dc_reads: int = 0

    async def scalar(self, statement: object) -> object:
        assert statement is DB_TIME
        self.owner.calls.append("time")
        return NOW

    async def execute(  # noqa: C901, PLR0911
        self,
        statement: object,
        params: object = None,
    ) -> Result:
        del params
        names = {
            id(LOCK): "lock",
            id(SCOPE): "scope",
            id(LATEST): "latest",
            id(TARGETS): "targets",
            id(DISABLE): "disable",
            id(INVALIDATE): "invalidate",
            id(APPEND_DEACTIVATED): "deactivated",
            id(APPEND_RESTORED): "restored",
            id(PURGE): "purge",
            id(ZERO): "zero",
            id(VERIFY): "verify",
            id(DC_FINGERPRINT): "dc",
        }
        self.owner.calls.append(names[id(statement)])
        if statement is SCOPE:
            return Result({"platform": "manifold"})
        if statement is LATEST:
            return Result(("transition", self.owner.state))
        if statement is TARGETS:
            return Result(())
        if statement is DISABLE:
            return Result(rowcount=0 if self.owner.fail == "disable" else 1)
        if statement is APPEND_DEACTIVATED:
            return Result(rowcount=0 if self.owner.fail == "append" else 1)
        if statement is APPEND_RESTORED:
            return Result(rowcount=1)
        if statement is PURGE:
            return Result(7)
        if statement is ZERO:
            return Result({"content_count": 0, "title_body_url_hash_count": 0})
        if statement is DC_FINGERPRINT:
            self.dc_reads += 1
            return Result((8, "edge") if self.dc_reads == 1 else self.owner.dc_after)
        if statement is VERIFY:
            return Result(
                {
                    "content_zero": self.owner.fail != "verify",
                    "search_zero": True,
                    "disabled": True,
                    "cleared": True,
                    "dcinside_intact": True,
                    "revision": "20260727_0010",
                    "latest_state": "restore_writing",
                }
            )
        return Result()


def database(
    engine: FakeEngine,
    proof: PrivacyProofSession,
) -> PrivacyDatabaseAdapter:
    """Inject a typed fake through the concrete adapter boundary."""
    return PrivacyDatabaseAdapter(
        engine=cast("AsyncEngine", cast("object", engine)),
        proof=proof,
    )


__all__ = ("FakeEngine", "database")
