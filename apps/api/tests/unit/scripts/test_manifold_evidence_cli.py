from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

import pytest
from app.services.configuration.manifold_evidence import (
    JSON_DOCUMENT,
    AuthorizationRecord,
    CliArgs,
    CommentContent,
    CommentProjection,
    LiveProof,
    MarketProjection,
    load_record,
    verify_record,
)
from pydantic import ValidationError

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).resolve().parents[5]
SCRIPT = ROOT / "apps" / "api" / "scripts" / "manifold_evidence.py"
EVIDENCE = ROOT / "docs" / "evidence" / "manifold-authorization.json"
DB_NOW = datetime(2026, 7, 28, 3, tzinfo=UTC)


@runtime_checkable
class ManifoldEvidenceCli(Protocol):
    def verify_command(self, args: CliArgs, *, refresh: bool) -> int: ...


@runtime_checkable
class CurrentLoopPolicy(Protocol):
    def get_event_loop(self) -> asyncio.AbstractEventLoop: ...
    def set_event_loop(self, loop: asyncio.AbstractEventLoop | None) -> None: ...


def load_cli_module() -> ManifoldEvidenceCli:
    spec = spec_from_file_location("manifold_evidence_cli", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module: ModuleType = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert isinstance(module, ManifoldEvidenceCli)
    return module


def live_proof(prepared_at: datetime) -> LiveProof:
    return LiveProof(
        schema="manifold.live-proof.v1",
        prepared_at=prepared_at,
        routes=("/v0/markets", "/v0/comments"),
        market=MarketProjection(
            id="market-1",
            question="Will this remain public?",
            market_slug="public-market",
            neutral_url="https://manifold.markets/market/public-market",
        ),
        comment=CommentProjection(
            id="comment-1",
            contractId="market-1",
            createdTime=1_722_132_000_000,
            content=CommentContent(text="public comment"),
        ),
        neutral_url_resolves_to_market_id=True,
        request_count=3,
        raw_body_persisted=False,
        projection_sha256="a" * 64,
    )


def test_script_runs_from_repository_root_and_fails_closed_without_database_env(
    tmp_path: Path,
) -> None:
    # Given: the documented repository-root invocation and no database secret.
    env = os.environ.copy()
    _ = env.pop("MISSING_MANIFOLD_DATABASE_URL", None)
    command = (
        sys.executable,
        str(SCRIPT),
        "verify",
        "--database-url-env",
        "MISSING_MANIFOLD_DATABASE_URL",
        "--evidence",
        str(EVIDENCE),
        "--live-proof",
        str(ROOT / "docs" / "evidence" / "manifold-live-redacted.json"),
        "--json-out",
        str(tmp_path / "receipt.json"),
    )

    # When: the committed script is launched from the repository root.
    completed = subprocess.run(  # noqa: S603 -- argv uses only trusted fixed paths.
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: the CLI boundary reports HOLD instead of failing during import.
    assert completed.returncode == 2
    assert "database URL environment variable is empty" in completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr


def test_refresh_uses_the_single_database_time_as_proof_preparation_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: a database clock that differs from any process-local wall clock.
    manifold_evidence = load_cli_module()

    async def fake_db_now(database_url: str) -> datetime:
        assert database_url == "postgresql+asyncpg://redacted"
        return DB_NOW

    prepared_times: list[datetime] = []

    def fake_probe(prepared_at: datetime) -> LiveProof:
        prepared_times.append(prepared_at)
        return live_proof(prepared_at)

    def fake_database_url(env_name: str | None) -> str:
        assert env_name == "DATABASE_URL"
        return "postgresql+asyncpg://redacted"

    monkeypatch.setattr(manifold_evidence, "database_url", fake_database_url)
    monkeypatch.setattr(manifold_evidence, "_db_now", fake_db_now)
    monkeypatch.setattr(manifold_evidence, "_probe", fake_probe)
    args = CliArgs(
        command="refresh",
        database_url_env="DATABASE_URL",
        evidence=str(EVIDENCE),
        json_out=str(tmp_path / "receipt.json"),
    )

    # When: refresh prepares and evaluates one live proof.
    exit_code = manifold_evidence.verify_command(args, refresh=True)

    # Then: no client clock can influence the preparation predicate.
    assert exit_code == 0
    assert prepared_times == [DB_NOW]
    assert (tmp_path / "receipt.json").is_file()


def test_verify_preserves_and_does_not_close_the_current_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: pytest owns a current loop and the database coroutine exposes its loop.
    manifold_evidence = load_cli_module()
    worker_loops: list[asyncio.AbstractEventLoop] = []

    async def fake_db_now(database_url: str) -> datetime:
        assert database_url == "postgresql+asyncpg://redacted"
        worker_loops.append(asyncio.get_running_loop())
        return DB_NOW

    def fake_database_url(env_name: str | None) -> str:
        assert env_name == "DATABASE_URL"
        return "postgresql+asyncpg://redacted"

    monkeypatch.setattr(manifold_evidence, "database_url", fake_database_url)
    monkeypatch.setattr(manifold_evidence, "_db_now", fake_db_now)
    monkeypatch.setattr(manifold_evidence, "_probe", live_proof)
    args = CliArgs(
        command="refresh",
        database_url_env="DATABASE_URL",
        evidence=str(EVIDENCE),
        json_out=str(tmp_path / "receipt.json"),
    )
    policy_candidate = getattr(asyncio.events, "_event_loop_policy", None)
    assert isinstance(policy_candidate, CurrentLoopPolicy)
    policy = policy_candidate
    policy_local = getattr(policy, "_local", None)
    previous_loop = cast(
        "asyncio.AbstractEventLoop | None",
        getattr(policy_local, "_loop", None),
    )
    loop_type = cast(
        "type[asyncio.AbstractEventLoop]",
        getattr(asyncio, "ProactorEventLoop", asyncio.SelectorEventLoop),
    )
    sentinel_loop = loop_type()
    policy.set_event_loop(sentinel_loop)
    try:
        exit_code = manifold_evidence.verify_command(args, refresh=True)

        # Then: the caller's loop survives and the private worker loop is closed.
        assert exit_code == 0
        assert policy.get_event_loop() is sentinel_loop
        assert not sentinel_loop.is_closed()
        assert len(worker_loops) == 1
        assert worker_loops[0] is not sentinel_loop
        assert worker_loops[0].is_closed()
    finally:
        policy.set_event_loop(previous_loop)
        sentinel_loop.close()


def test_missing_recheck_field_is_rejected_at_the_file_boundary() -> None:
    # Given: an evidence document with its authoritative recheck field removed.
    document = JSON_DOCUMENT.validate_json(EVIDENCE.read_bytes())
    del document["recheck_at"]

    # When/Then: schema parsing fails before authorization can be evaluated.
    with pytest.raises(ValidationError):
        _ = AuthorizationRecord.model_validate(document)


def test_stale_evidence_fails_the_complete_authorization_decision() -> None:
    # Given: a valid record and proof at the exclusive 24-hour boundary.
    record = load_record(EVIDENCE, AuthorizationRecord)
    db_now = record.retrieved_at + timedelta(hours=24)

    # When: the complete proof is checked using that database time.
    authorized, reasons = verify_record(record, live_proof(db_now), db_now)

    # Then: activation is held specifically for stale authorization evidence.
    assert not authorized
    assert "evidence_stale" in reasons


def test_recheck_interval_one_microsecond_short_fails_authorization() -> None:
    # Given: a hash-addressed record whose recheck interval is under 33 days.
    record = load_record(EVIDENCE, AuthorizationRecord)
    shortened = record.model_copy(
        update={
            "recheck_at": record.retrieved_at
            + timedelta(days=33)
            - timedelta(microseconds=1)
        }
    )

    # When: its authorization predicates are evaluated.
    authorized, reasons = verify_record(shortened, live_proof(DB_NOW), DB_NOW)

    # Then: the authoritative minimum interval independently fails closed.
    assert not authorized
    assert "recheck_interval_short" in reasons
