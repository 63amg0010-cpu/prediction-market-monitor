from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app.services.release import source_activation_cli as cli  # noqa: E402
from apps.api.app.services.release.source_activation_domain import (  # noqa: E402
    ActivationState,
)
from apps.api.app.services.release.source_activation_receipts import (  # noqa: E402
    ActivationOutput,
)
from apps.api.app.services.release.source_activation_state import (  # noqa: E402
    LockedActivationState,
)
from pydantic import ValidationError  # noqa: E402

if TYPE_CHECKING:
    from types import TracebackType

    from apps.api.app.services.release.source_activation_commands import PhaseContext

SCRIPT = ROOT / "apps" / "api" / "scripts" / "fresh_search_release_gate.py"
NONCE = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DB_NOW = datetime(2026, 7, 28, 3, 17, tzinfo=UTC)


def test_activate_help_exposes_every_phase_through_real_subprocess() -> None:
    # Given: the committed release-gate script.
    command = (sys.executable, str(SCRIPT), "activate", "--help")

    # When: an operator asks the real process surface for activation help.
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: all four executable phases are exposed by a successful parser.
    assert completed.returncode == 0
    assert "{reserve,commit,reprepare,restore}" in completed.stdout


def test_activate_fails_closed_when_database_env_is_empty(tmp_path: Path) -> None:
    # Given: a complete reserve argv without its database credential.
    env = os.environ.copy()
    _ = env.pop("MISSING_RELEASE_DATABASE_URL", None)
    command = (
        sys.executable,
        str(SCRIPT),
        "activate",
        "--phase",
        "reserve",
        "--database-url-env",
        "MISSING_RELEASE_DATABASE_URL",
        "--activation-nonce",
        str(NONCE),
        "--expected-sha",
        "a" * 40,
        "--attestation",
        str(tmp_path / "attestation.json"),
        "--free-tier-result",
        str(tmp_path / "free-tier.json"),
        "--binding-handshake-receipt",
        str(tmp_path / "handshake.json"),
        "--json-out",
        str(tmp_path / "reserve.json"),
    )

    # When: the actual process enters the activation boundary.
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: it reports one bounded HOLD without creating an output.
    assert completed.returncode == 2
    assert completed.stderr == "activation HOLD: database_url_environment_empty\n"
    assert not (tmp_path / "reserve.json").exists()


def test_chain_receipt_rejects_unknown_fields() -> None:
    # Given: one field outside the closed receipt schema.
    document = {
        "schema_version": 1,
        "command": "handshake-github",
        "accepted": True,
        "activation_nonce": str(NONCE),
        "reviewed_sha": "a" * 40,
        "state_after": "handshake_passed",
        "payload_sha256": "b" * 64,
        "predecessor_receipt_sha256": "c" * 64,
        "receipt_sha256": "d" * 64,
        "secret": "must-not-pass",
    }

    # When/Then: the trust boundary rejects the extra key.
    with pytest.raises(ValidationError):
        _ = cli.ChainReceipt.model_validate(document)


def test_execute_encloses_lock_state_load_and_phase_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: a stub database engine and a reserve phase observable.
    events: list[str] = []

    class StubConnection:
        pass

    connection = StubConnection()

    class StubTransaction:
        async def __aenter__(self) -> StubConnection:
            events.append("transaction_begin")
            return connection

        async def __aexit__(
            self,
            _exception_type: type[BaseException] | None,
            _exception: BaseException | None,
            _traceback: TracebackType | None,
        ) -> None:
            events.append("transaction_commit")

    class StubEngine:
        def begin(self) -> StubTransaction:
            return StubTransaction()

        async def dispose(self) -> None:
            events.append("engine_dispose")

    async def database_now_locked(_connection: StubConnection) -> datetime:
        events.append("database_now_locked")
        return DB_NOW

    async def load_state(_connection: StubConnection) -> LockedActivationState:
        events.append("load_current_state")
        return LockedActivationState(
            state=ActivationState(
                activation_nonce=NONCE,
                attestation_generation=1,
                attestation_sha256="a" * 64,
                prepared_at=DB_NOW - timedelta(minutes=20),
                state="handshake_passed",
                source_enabled=False,
                active_authorization_id=None,
                current_budget_id=None,
                current_binding_id=None,
                current_cadence_id=None,
                binding_write_occurred=True,
                restore_verified=False,
            ),
            transition_id=UUID("11111111-1111-4111-8111-111111111111"),
            attestation_id=UUID("22222222-2222-4222-8222-222222222222"),
            binding_intent_id=UUID("33333333-3333-4333-8333-333333333333"),
            binding_payload_sha256="b" * 64,
            cadence_id=None,
        )

    async def reserve_phase(_context: PhaseContext) -> ActivationOutput:
        events.append("reserve")
        return ActivationOutput(
            command="activation-reserve",
            accepted=True,
            activation_nonce=NONCE,
            reviewed_sha="a" * 40,
            db_now=DB_NOW.isoformat(),
            state_before="handshake_passed",
            state_after="anchor_reserved",
            attestation_generation=1,
            attestation_sha256="a" * 64,
            predecessor_receipt_sha256="c" * 64,
            cadence_anchor_at=(DB_NOW + timedelta(hours=3)).isoformat(),
            reason=None,
            receipt_sha256="d" * 64,
        )

    monkeypatch.setenv("RELEASE_TEST_DATABASE_URL", "postgresql+asyncpg://redacted")

    def engine_factory(_url: str) -> StubEngine:
        return StubEngine()

    monkeypatch.setattr(cli, "create_async_engine", engine_factory)
    monkeypatch.setattr(
        cli.activation_db,
        "database_now_locked",
        database_now_locked,
    )
    monkeypatch.setattr(cli, "load_current_state", load_state)
    monkeypatch.setattr(cli, "reserve", reserve_phase)
    args = cli.parse_args(
        [
            "activate",
            "--phase",
            "reserve",
            "--database-url-env",
            "RELEASE_TEST_DATABASE_URL",
            "--activation-nonce",
            str(NONCE),
            "--expected-sha",
            "a" * 40,
            "--attestation",
            str(tmp_path / "attestation.json"),
            "--free-tier-result",
            str(tmp_path / "free-tier.json"),
            "--binding-handshake-receipt",
            str(tmp_path / "handshake.json"),
            "--json-out",
            str(tmp_path / "reserve.json"),
        ]
    )

    # When: the CLI runtime executes the phase.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        loop = runner.get_loop()
        exit_code = runner.run(cli.execute(args))

    # Then: the lock and state read are inside one committing transaction.
    assert loop.is_closed()
    assert exit_code == 0
    assert events == [
        "transaction_begin",
        "database_now_locked",
        "load_current_state",
        "reserve",
        "transaction_commit",
        "engine_dispose",
    ]
    assert (tmp_path / "reserve.json").is_file()
