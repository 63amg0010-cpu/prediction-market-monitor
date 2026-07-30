from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))

from scripts.runtime_privacy_adapter import (
    PrivacyProofSession,
    PrivacyRuntimeError,
)
from tests.unit.scripts.todo11_privacy_adapter_fakes import FakeEngine, database
from tests.unit.scripts.todo11_privacy_stubs import scope


@pytest.mark.asyncio
async def test_database_contain_happy_and_transaction_rollback() -> None:
    good = FakeEngine("active")
    receipt = await database(good, PrivacyProofSession()).contain(scope())
    assert receipt.state == "deactivated"
    assert good.calls[:3] == ["lock", "scope", "latest"]
    assert good.calls[-3:] == ["disable", "invalidate", "deactivated"]
    assert good.commits == 1

    failed = FakeEngine("active")
    failed.fail = "append"
    with pytest.raises(PrivacyRuntimeError, match="PRIVACY_HOLD"):
        _ = await database(failed, PrivacyProofSession()).contain(scope())
    assert failed.rollbacks == 1


@pytest.mark.asyncio
async def test_database_purge_happy_and_dcinside_drift_fails_closed() -> None:
    good = FakeEngine("deactivated")
    receipt = await database(good, PrivacyProofSession()).purge(
        scope(),
        "a" * 64,
    )
    assert receipt.deleted_row_count == 7
    assert receipt.zero_title_body_url_hashes

    drift = FakeEngine("deactivated")
    drift.dc_after = (7, "changed")
    with pytest.raises(PrivacyRuntimeError, match="database_purge_incomplete"):
        _ = await database(drift, PrivacyProofSession()).purge(
            scope(),
            "a" * 64,
        )
    assert drift.rollbacks == 1


@pytest.mark.asyncio
async def test_verify_shared_proof_allows_only_privacy_restore() -> None:
    proof, engine = PrivacyProofSession(), FakeEngine("restore_writing")
    db = database(engine, proof)
    db_result = await db.verify(scope())
    proof.record("github", scope(), "d" * 64, accepted=True)
    proof.record("provider", scope(), "e" * 64, accepted=True)
    restored = await db.append_restored(scope(), "b" * 64, "c" * 64)
    assert restored.state == "restored"
    assert str(scope().source_id) not in f"{db_result}{restored}"


@pytest.mark.asyncio
async def test_verify_failure_and_ordinary_restore_shortcut_are_holds() -> None:
    proof, engine = PrivacyProofSession(), FakeEngine("restore_writing")
    db = database(engine, proof)
    engine.fail = "verify"
    result = await db.verify(scope())
    assert not result.database_content_zero
    before = engine.begins
    with pytest.raises(PrivacyRuntimeError, match="privacy_proof"):
        _ = await db.append_restored(scope(), "b" * 64, "c" * 64)
    assert engine.begins == before
