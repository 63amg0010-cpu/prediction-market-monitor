from __future__ import annotations

# ruff: noqa: E402
import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import release_runtime_compat_handler as handlers

NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)
ACTIVATION = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


@dataclass(frozen=True)
class Snapshot:
    state: object
    observed_at: datetime


class ReachedDocumentReadError(RuntimeError):
    pass


def test_compat_state_preserves_callers_current_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Engine:
        async def dispose(self) -> None:
            return None

    async def snapshot(_engine: object, _nonce: UUID) -> object:
        return Snapshot(
            state=object(),
            observed_at=NOW,
        )

    def engine_from_name(_name: str) -> Engine:
        return Engine()

    def stop_at_document_read(_path: str) -> dict[str, object]:
        raise ReachedDocumentReadError

    monkeypatch.setattr(handlers, "engine_from_named_env", engine_from_name)
    monkeypatch.setattr(handlers, "rollback_database_snapshot", snapshot)
    monkeypatch.setattr(handlers, "read_document", stop_at_document_read)
    args = argparse.Namespace(
        cadence_anchor_at=(NOW - timedelta(days=1)).isoformat(),
        database_url_env="DATABASE_URL",
        activation_nonce=str(ACTIVATION),
        api_alias_receipt="stop",
    )
    policy = cast(
        "asyncio.AbstractEventLoopPolicy",
        vars(asyncio.events)["_event_loop_policy"],
    )
    policy_local = getattr(policy, "_local", None)
    previous_loop = cast(
        "asyncio.AbstractEventLoop | None",
        getattr(policy_local, "_loop", None),
    )
    caller_loop = asyncio.new_event_loop()
    policy.set_event_loop(caller_loop)
    try:
        with pytest.raises(ReachedDocumentReadError):
            _ = handlers.compat_state(args, now=lambda: NOW)

        assert asyncio.get_event_loop() is caller_loop
        assert not caller_loop.is_closed()
    finally:
        policy.set_event_loop(previous_loop)
        caller_loop.close()
